"""Wrapper for RXN4Chem functionalities."""

import logging
import ast
import re
from time import sleep

from rxn4chemistry import RXN4ChemistryWrapper  # type: ignore

from chemagent.utils.error import *
from chemagent.llms import GptRequester
from chemagent.utils import is_smiles
from .base import BaseTool

__all__ = ["ForwardSynthesis", "RXNRetrosynthesis"]


logger = logging.getLogger(__name__)


class RXN4Chem(BaseTool):
    """Wrapper for RXN4Chem functionalities."""

    name: str
    description: str

    base_url: str = "https://rxn.res.ibm.com"
    sleep_time: int = 5

    rxn4chem_chemistry_wrapper = None

    def __init__(self, rxn4chem_api_key, init=True, interface='text'):
        """Init object."""
        super().__init__(init, interface=interface)

        self.rxn4chem_api_key = rxn4chem_api_key
        if RXN4Chem.rxn4chem_chemistry_wrapper is None:
            RXN4Chem.rxn4chem_chemistry_wrapper = RXN4ChemistryWrapper(
                api_key=self.rxn4chem_api_key, base_url=RXN4Chem.base_url
            )
            RXN4Chem.rxn4chem_chemistry_wrapper.create_project('ChemAgent')
        self.rxn4chem = RXN4Chem.rxn4chem_chemistry_wrapper
        if init:
            assert self.rxn4chem.project_id is not None

    @staticmethod
    def retry(times: int, exceptions, sleep_time: int = 5):
        """
        Retry Decorator.

        Retries the wrapped function/method `times` times if the exceptions
        listed in ``exceptions`` are thrown
        :param times: The number of times to repeat the wrapped function/method
        :type times: Int
        :param Exceptions: Lists of exceptions that trigger a retry attempt
        :type Exceptions: Tuple of Exceptions
        """

        def decorator(func):
            def newfn(*args, **kwargs):
                attempt = 0
                while attempt < times:
                    try:
                        sleep(sleep_time)
                        return func(*args, **kwargs)
                    except exceptions:
                        print(
                            "Exception thrown when attempting to run %s, "
                            "attempt %d of %d" % (func, attempt, times)
                        )
                        attempt += 1
                return func(*args, **kwargs)

            return newfn

        return decorator


class ForwardSynthesis(RXN4Chem):
    """Predict reaction."""

    name = "ForwardSynthesis"
    func_name = "do_forward_synthesis"
    description = (
        "Predict the product of a chemical reaction. "
        "Input the SMILES of the reactants and reagents separated by a dot '.', returns SMILES of the products."
    )
    func_doc = ("reactants: str", "str")
    func_description = description
    examples = [
        {'input': 'CCN.CN1C=CC=C1C=O', 'output': 'CCNCc1cccn1C'},
    ]

    def _run_text(self, reactants: str) -> str:
        return self._run_base(reactants)

    def _run_base(self, reactants: str, *args, **kwargs) -> str:
        """Run reaction prediction."""
        # Check that input is smiles
        if not is_smiles(reactants):
            raise ChemAgentInputError("The input contains invalid SMILES. Please double-check.")
        if '.' not in reactants:
            raise ChemAgentInputError("The input should contain at least two reactants and reagents separated by a dot '.'. Please double-check.")

        prediction_id = self.predict_reaction(reactants)
        results = self.get_results(prediction_id)
        product = results["productMolecule"]["smiles"]
        return product

    @RXN4Chem.retry(10, ChemAgentToolProcessError)
    def predict_reaction(self, reactants: str) -> str:
        """Make api request."""
        response = self.rxn4chem.predict_reaction(reactants)
        if "prediction_id" in response.keys():
            return response["prediction_id"]
        else:
            raise ChemAgentToolProcessError("The tool failed to predict the reaction. Maybe the input is invalid. Please make sure the input is valid SMILES of reactants separated by dot '.' and try again.")

    @RXN4Chem.retry(10, ChemAgentOutputError)
    def get_results(self, prediction_id: str) -> str:
        """Make api request."""
        results = self.rxn4chem.get_predict_reaction_results(prediction_id)
        if "payload" in results["response"].keys():
            return results["response"]["payload"]["attempts"][0]
        else:
            raise ChemAgentOutputError("Error in obtaining the results. Maybe the input is invalid. Please make sure the input is valid SMILES of reactants separated by dot '.' and try again.")


class RXNRetrosynthesis(RXN4Chem):
    """Predict retrosynthesis."""

    name = "ReactionRetrosynthesis"
    func_name = "do_retrosynthesis"
    description = (
        "Obtain the synthetic route to a chemical compound. "
        "Takes as input the SMILES of the product, returns textual description of how to synthesize it."
    )
    openai_api_key: str = ""

    def __init__(self, rxn4chem_api_key, openai_api_key, init=True, interface='text'):
        """Init object."""
        raise NotImplementedError("This tool is not yet verified.")
        super().__init__(rxn4chem_api_key, init=init, interface=interface)
        self.openai_api_key = openai_api_key

    def _run_base(self, target: str, *args, **kwargs) -> str:
        """Run retrosynthesis prediction."""
        # Check that input is smiles
        if not is_smiles(target):
            return "Incorrect input."

        prediction_id = self.predict_retrosynthesis(target)
        paths = self.get_paths(prediction_id)
        # path_img = self.visualize_path(paths[0])
        # print('====Paths====')
        # print(paths)
        # print('============')
        procedure = self.get_action_sequence(paths[0])
        return procedure

    @RXN4Chem.retry(10, KeyError)
    def predict_retrosynthesis(self, target: str) -> str:
        """Make api request."""
        response = self.rxn4chem.predict_automatic_retrosynthesis(
            product=target,
            fap=0.6,
            max_steps=3,
            nbeams=10,
            pruning_steps=2,
            ai_model="12class-tokens-2021-05-14",
        )
        if "prediction_id" in response.keys():
            return response["prediction_id"]
        raise KeyError

    @RXN4Chem.retry(20, (KeyError, AttributeError))
    def get_paths(self, prediction_id: str) -> str:
        """Make api request."""
        results = self.rxn4chem.get_predict_automatic_retrosynthesis_results(
            prediction_id
        )
        if "retrosynthetic_paths" not in results.keys():
            raise KeyError
        paths = results["retrosynthetic_paths"]
        if paths is not None:
            if len(paths) > 0:
                return paths
        if results["status"] == "PROCESSING":
            sleep(self.sleep_time * 2)
            raise KeyError
        raise KeyError

    def get_action_sequence(self, path):
        """Get sequence of actions."""
        response = self.synth_from_sequence(path["sequenceId"])
        if "synthesis_id" not in response.keys():
            return path

        synthesis_id = response["synthesis_id"]
        nodeids = self.get_node_ids(synthesis_id)
        if nodeids is None:
            return "Tool error"

        # Attempt to get actions for each node + product information
        real_nodes = []
        actions_and_products = []
        for node in nodeids:
            node_resp = self.get_reaction_settings(
                synthesis_id=synthesis_id, node_id=node
            )
            if "actions" in node_resp.keys():
                real_nodes.append(node)
                actions_and_products.append(node_resp)

        json_actions = self._preproc_actions(actions_and_products)
        llm_sum = self._summary_gpt(json_actions)
        return llm_sum

    @RXN4Chem.retry(20, KeyError)
    def synth_from_sequence(self, sequence_id: str) -> str:
        """Make api request."""
        response = self.rxn4chem.create_synthesis_from_sequence(sequence_id=sequence_id)
        if "synthesis_id" in response.keys():
            return response
        raise KeyError

    @RXN4Chem.retry(20, KeyError)
    def get_node_ids(self, synthesis_id: str):
        """Make api request."""
        response = self.rxn4chem.get_node_ids(synthesis_id=synthesis_id)
        if isinstance(response, list):
            if len(response) > 0:
                return response
        return KeyError

    @RXN4Chem.retry(20, KeyError)
    def get_reaction_settings(self, synthesis_id: str, node_id: str):
        """Make api request."""
        response = self.rxn4chem.get_reaction_settings(
            synthesis_id=synthesis_id, node_id=node_id
        )
        if "actions" in response.keys():
            return response
        elif "response" in response.keys():
            if "error" in response["response"].keys():
                if response["response"]["error"] == "Too Many Requests":
                    sleep(self.sleep_time * 2)
                    raise KeyError
            return response
        raise KeyError

    def _preproc_actions(self, actions_and_products):
        """Preprocess actions."""
        json_actions = {"number_of_steps": len(actions_and_products)}

        for i, actn in enumerate(actions_and_products):
            json_actions[f"Step_{i}"] = {}
            json_actions[f"Step_{i}"]["actions"] = actn["actions"]
            json_actions[f"Step_{i}"]["product"] = actn["product"]

        # Clean actions to use less tokens: Remove False, None, ''
        clean_act_str = re.sub(
            r"\'[A-Za-z]+\': (None|False|\'\'),? ?", "", str(json_actions)
        )
        json_actions = ast.literal_eval(clean_act_str)

        return json_actions

    def _summary_gpt(self, json: dict) -> str:
        """Describe synthesis."""
        # llm = ChatOpenAI(  # type: ignore
        #     temperature=0.05,
        #     model_name="gpt-3.5-turbo-16k",
        #     request_timeout=2000,
        #     max_tokens=2000,
        #     openai_api_key=self.openai_api_key,
        # )
        llm = GptRequester(api_code=self.openai_api_key, model_name="gpt-3.5-turbo-16k")
        prompt = (
            "Here is a chemical synthesis described as a json.\nYour task is "
            "to describe the synthesis, as if you were giving instructions for"
            "a recipe. Use only the substances, quantities, temperatures and "
            "in general any action mentioned in the json file. This is your "
            "only source of information, do not make up anything else. Also, "
            "add 15mL of DCM as a solvent in the first step. If you ever need "
            'to refer to the json file, refer to it as "(by) the tool". '
            "However avoid references to it. \nFor this task, give as many "
            f"details as possible.\n {str(json)}"
        )
        conversation = [
            {'role': 'user', 'content': prompt}
        ]
        return llm.request(conversation)[0]

    def _path_to_dict(self, path):
        """Convert path to dict."""
        if len(path["children"]) != 0:
            in_stock = False
            rxn_smi = path["smiles"] + ">>"
            for prec in path["children"]:
                rxn_smi += prec["smiles"] + "."
            rxn_smi = rxn_smi[:-1]

            children = [
                {
                    "type": "reaction",
                    "hide": False,
                    "smiles": rxn_smi,
                    "is_reaction": True,
                    "metadata": {},
                    "children": [self._path_to_dict(c) for c in path["children"]],
                }
            ]
        else:
            in_stock = True
            children = []

        return {
            "type": "mol",
            "route_metadata": {"created_at_iteration": 1, "is_solved": True},
            "hide": False,
            "smiles": path["smiles"],
            "is_chemical": True,
            "in_stock": in_stock,
            "children": children,
        }


class Retrosynthesis(RXN4Chem):
    """Predict single-step retrosynthesis."""

    name = "Retrosynthesis"
    func_name = "do_retrosynthesis"
    description = (
        "Conduct single-step retrosynthesis."
        "Input SMILES of product, returns SMILES of potential reactants separated by a dot '.' as well as the confidence. Will output multiple sets of reactants if applicable."
    )
    func_doc = ("product: str", "str")
    func_description = description
    examples = [
        {'input': 'CCO', 'output': 'There are 13 possible sets of reactants for the given product:\n1.\tReactants: C1CCOC1.CCNC(=O)c1cccn1C.[Li][AlH4]\tConfidence: 1.0\n2.\tReactants: CCN.CCO.Cn1cccc1C=O.[BH4-].[Na+]\tConfidence: 1.0\n3.\tReactants: CCN.CO.Cn1cccc1C=O.[BH4-].[Na+]\tConfidence: 1.0\n4.\tReactants: CCN.Cn1cccc1C=O.[BH4-].[Na+]\tConfidence: 1.0\n5.\tReactants: CCN.CCO.Cn1cccc1C=O.O.[BH4-].[Na+]\tConfidence: 1.0\n6.\tReactants: CCN.CO.Cn1cccc1C=O.O.[BH4-].[Na+]\tConfidence: 1.0\n7.\tReactants: C1CCOC1.CCN.Cn1cccc1C=O.[BH4-].[Na+]\tConfidence: 1.0\n8.\tReactants: CCN.Cl.Cn1cccc1C=O\tConfidence: 0.938\n9.\tReactants: CCN.Cn1cccc1C=O\tConfidence: 0.917\n10.\tReactants: CCN.Cl.Cn1cccc1C=O\tConfidence: 0.841\n11.\tReactants: C1CCOC1.CCN.Cn1cccc1C=O\tConfidence: 0.797\n12.\tReactants: C1CCOC1.CCN.CO.Cn1cccc1C=O\tConfidence: 0.647\n13.\tReactants: C1CCOC1.CC(=O)NCc1cccn1C.[Li][AlH4]\tConfidence: 1.0\n'},  
    ]

    def _run_base(self, target: str, *args, **kwargs) -> str:
        """Run retrosynthesis prediction."""
        # Check that input is smiles
        if not is_smiles(target):
            raise ChemAgentInputError("The input contains invalid SMILES. Please double-check.")

        prediction_id = self.predict_retrosynthesis(target)
        paths = self.get_paths(prediction_id)
        result = "There %s %d possible sets of reactants for the given product:\n" % (
            "are" if len(paths) > 1 else "is",
            len(paths),
        )
        result_list = []
        for idx, path in enumerate(paths, start=1):
            children_smiles, confidence = self._get_children_smiles_and_confidence(path)
            result_list.append((children_smiles, confidence))
        result_list.sort(key=lambda x: x[1], reverse=True)
        for idx, (children_smiles, confidence) in enumerate(result_list, start=1):
            result += f"{idx}.\tReactants: {children_smiles}\tConfidence: {confidence}\n"
        return result

    @RXN4Chem.retry(10, KeyError)
    def predict_retrosynthesis(self, target: str) -> str:
        """Make api request."""
        response = self.rxn4chem.predict_automatic_retrosynthesis(
            product=target,
            max_steps=1,
        )
        if "prediction_id" in response.keys():
            return response["prediction_id"]
        raise KeyError

    @RXN4Chem.retry(20, ChemAgentOutputError)
    def get_paths(self, prediction_id: str) -> str:
        """Make api request."""
        results = self.rxn4chem.get_predict_automatic_retrosynthesis_results(
            prediction_id
        )
        if "retrosynthetic_paths" not in results.keys():
            raise ChemAgentOutputError("Error in obtaining the results. Maybe the input is invalid. Please make sure the input is valid SMILES and try again.")
        paths = results["retrosynthetic_paths"]
        if paths is not None:
            if len(paths) > 0:
                return paths
        if results["status"] == "PROCESSING":
            sleep(self.sleep_time * 2)
        raise ChemAgentOutputError("Error in obtaining the results. Maybe the input is invalid. Please make sure the input is valid SMILES and try again.")
    
    def _get_children_smiles_and_confidence(self, path):
        children = path['children']
        children_smiles = []
        for child in children:
            smiles = child['smiles']
            children_smiles.append(smiles)
        return '.'.join(children_smiles), path['confidence']
