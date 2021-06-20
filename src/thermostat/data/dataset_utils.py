from collections import defaultdict

import datasets
from datasets import Dataset
from functools import reduce
from itertools import groupby
from overrides import overrides
from spacy import displacy
from tqdm import tqdm
from transformers import AutoTokenizer
from typing import Dict, List

from thermostat.data import thermostat_configs
from thermostat.data.tokenization import fuse_subwords
from thermostat.utils import lazy_property
from thermostat.visualize import ColorToken, Heatmap, normalize_attributions


def list_configs():
    """ Returns the list of names of all available configs in the Thermostat HF dataset"""
    return [config.name for config in thermostat_configs.builder_configs]


def get_config(config_name):
    """ based on : https://stackoverflow.com/a/7125547 """
    return next((x for x in thermostat_configs.builder_configs if x.name == config_name), None)


def get_text_fields(config_name):
    text_fields = get_config(config_name).text_column
    if type(text_fields) != list:
        text_fields = [text_fields]
    return text_fields


def load(config_str: str = None):
    assert config_str, f'Please enter a config. Available options: {list_configs()}.'

    def load_from_single_config(config):
        print(f'Loading Thermostat configuration: {config}')
        return datasets.load_dataset("hf_dataset.py", config, split="test")

    if config_str in list_configs():
        data = load_from_single_config(config_str)

    elif config_str in ['-'.join(c.split('-')[:2]) for c in list_configs()]:
        # Resolve "dataset+model" to all explainer subsets
        raise NotImplementedError()

    elif config_str in [f'{c.split("-")[0]}-{c.split("-")[-1]}' for c in list_configs()]:
        # Resolve "dataset+explainer" to all model subsets
        raise NotImplementedError()

    else:
        raise ValueError(f'Invalid config. Available options: {list_configs()}')

    return Thermopack(data)


def get_coordinate(thermostat_dataset: Dataset, coordinate: str) -> str:
    """ Determine a coordinate (dataset, model, or explainer) of a Thermostat dataset from its description """
    assert coordinate in ['Model', 'Dataset', 'Explainer']
    coord_prefix = f'{coordinate}: '
    assert coord_prefix in thermostat_dataset.description
    str_post_coord_prefix = thermostat_dataset.description.split(coord_prefix)[1]
    if '\n' in str_post_coord_prefix:
        coord_value = str_post_coord_prefix.split('\n')[0]
    else:
        coord_value = str_post_coord_prefix
    return coord_value


class ThermopackMeta(type):
    """ Inspired by: https://stackoverflow.com/a/65917858 """
    def __new__(mcs, name, bases, dct):
        child = super().__new__(mcs, name, bases, dct)
        for base in bases:
            for field_name, field in base.__dict__.items():
                if callable(field) and not field_name.startswith('__'):
                    setattr(child, field_name, mcs.force_child(field, field_name, base, child))
        return child

    @staticmethod
    def force_child(fun, fun_name, base, child):
        """Turn from Base- to Child-instance-returning function."""
        def wrapper(*args, **kwargs):
            result = fun(*args, **kwargs)
            if not result:
                # Ignore if returns None
                return None
            if type(result) == base:
                print(fun_name)
                # Return Child instance if the Base method tries to return Base instance.
                return child(result)
            return result
        return wrapper


class Thermopack(Dataset, metaclass=ThermopackMeta):
    def __init__(self, hf_dataset):
        super().__init__(hf_dataset.data, info=hf_dataset.info, split=hf_dataset.split,
                         indices_table=hf_dataset._indices)
        self.dataset = hf_dataset

        # Model
        self.model_name = get_coordinate(hf_dataset, 'Model')

        # Dataset
        self.dataset_name = get_coordinate(hf_dataset, 'Dataset')
        self.label_names = hf_dataset.info.features['label'].names

        # Align label indices (some MNLI and XNLI models have a different order in the label names)
        label_classes = get_config(self.config_name).label_classes
        if label_classes != self.label_names:
            self.dataset = self.dataset.map(lambda instance: {
                'label': label_classes.index(self.label_names[instance['label']])})
            self.label_names = label_classes

        # Explainer
        self.explainer_name = get_coordinate(hf_dataset, 'Explainer')

    @lazy_property
    def tokenizer(self):
        return AutoTokenizer.from_pretrained(self.model_name)

    @lazy_property
    def units(self):
        units = []
        for instance in tqdm(self.dataset,
                             desc=f'Tokenizing {self.config_name} instances (Tokenizer: {self.model_name})'):
            # Decode labels and predictions
            true_label_index = instance['label']
            true_label = {'index': true_label_index,
                          'name': self.label_names[true_label_index]}

            predicted_label_index = instance['predictions'].index(max(instance['predictions']))
            predicted_label = {'index': predicted_label_index,
                               'name': self.label_names[predicted_label_index]}

            units.append(Thermounit(
                instance, true_label, predicted_label,
                self.model_name, self.dataset_name, self.explainer_name, self.tokenizer, self.config_name))
        return units

    @overrides
    def __getitem__(self, idx):
        """ Indexing a Thermopack returns a Thermounit """
        return self.units[idx]

    @overrides
    def __iter__(self):
        for unit in self.units:
            yield unit

    @overrides
    def __str__(self):
        return self.info.description


class Thermounit:
    """ Processed single instance of a Thermopack (Thermostat dataset/configuration) """
    def __init__(self, instance, true_label, predicted_label, model_name, dataset_name, explainer_name, tokenizer,
                 config_name):
        self.instance = instance
        self.index = self.instance['idx']
        self.attributions = self.instance['attributions']
        self.true_label = true_label
        self.predicted_label = predicted_label
        self.model_name = model_name
        self.dataset_name = dataset_name
        self.explainer_name = explainer_name
        self.tokenizer = tokenizer
        self.config_name = config_name
        self.text_fields: List = []
        self.texts: Dict = {}

    @property
    def tokens(self) -> Dict:
        # "tokens" includes all special tokens, later used for the heatmap when aligning with attributions
        tokens = self.tokenizer.convert_ids_to_tokens(self.instance['input_ids'])
        # Make token index
        tokens_enum = dict(enumerate(tokens))
        return tokens_enum

    def fill_text_fields(self, attributions=None, fuse_subwords_strategy='salient'):
        # Determine groups of tokens split by [SEP] tokens
        text_groups = []
        for group in [list(g) for k, g in groupby(self.tokens.items(),
                                                  lambda kt: kt[1] != self.tokenizer.sep_token) if k]:
            # Remove groups that only contain special tokens
            if len([t for t in group if t[1] in self.tokenizer.all_special_tokens]) < len(group):
                text_groups.append(group)

        # Set text_fields attribute, e.g. containing "premise" and "hypothesis"
        setattr(self, 'text_fields', get_text_fields(self.config_name))

        # In case this method gets called from somewhere else than the heatmap method, assign attributions from self
        if not attributions:
            attributions = self.attributions

        # Assign text field values based on groups
        for text_field, field_tokens in zip(self.text_fields, text_groups):
            # Create new list containing all non-special tokens
            non_special_tokens_enum = [t for t in field_tokens if t[1] not in self.tokenizer.all_special_tokens]
            # Select attributions according to token indices (tokens_enum keys)
            # TODO: Send token indices through fuse_words etc and replace None in ColorToken init
            selected_atts = [attributions[idx] for idx in [t[0] for t in non_special_tokens_enum]]
            non_special_tokens = [t[1] for t in non_special_tokens_enum]
            if fuse_subwords_strategy:
                tokens, atts = fuse_subwords(non_special_tokens, selected_atts, self.tokenizer,
                                             strategy=fuse_subwords_strategy)
            else:
                tokens, atts = non_special_tokens, selected_atts

            assert (len(tokens) == len(atts))
            # Cast each token into ColorToken objects with default color white which can later be overwritten
            # by a Heatmap object
            color_tokens = [ColorToken(token=token,
                                       attribution=att,
                                       text_field=text_field,
                                       token_index=None,  # TODO (see other TODO above)
                                       thermounit_vars=vars(self))
                            for token, att in zip(tokens, atts)]

            # Set class attribute with the name of the text field
            setattr(self, text_field, color_tokens)

        # Introduce a texts attribute that also stores all assigned text fields into a dict with the key being the
        # name of each text field
        setattr(self, 'texts', {text_field: getattr(self, text_field) for text_field in self.text_fields})

    @lazy_property
    def heatmap(self, gamma=1.0, normalize=True, flip_attributions_idx=None, fuse_subwords_strategy='salient'):
        """ Generate a list of tuples in the form of <token,color> for a single data point of a Thermostat dataset """

        # Handle attributions, apply normalization and sign flipping if needed
        atts = self.attributions
        if normalize:
            atts = normalize_attributions(atts)
        if flip_attributions_idx == self.predicted_label['index']:
            atts = [att * -1 for att in atts]

        # Use detokenizer to fill text fields
        self.fill_text_fields(attributions=atts, fuse_subwords_strategy=fuse_subwords_strategy)

        ctoken_fields = list(self.texts.values())
        ctokens = reduce(lambda x, y: x + y, ctoken_fields)
        heatmap = Heatmap(color_tokens=ctokens, gamma=gamma)

        return heatmap

    def render(self, attribution_labels=False, jupyter=False):
        """ Uses the displaCy visualization tool to render a HTML from the heatmap """

        full_html = ''
        for field_name, text_field_heatmap, in self.heatmap.items():
            print(f'Heatmap of text field "{field_name}"')
            ents = []
            colors = {}
            ii = 0
            for token_rgb in text_field_heatmap:
                token, rgb, att_rounded = token_rgb.values()

                ff = ii + len(token)

                # One entity in displaCy contains start and end markers (character index) and optionally a label
                # The label can be added by setting "attribution_labels" to True
                ent = {
                    'start': ii,
                    'end': ff,
                    'label': str(att_rounded),
                }

                ents.append(ent)
                # A "colors" dict takes care of the mapping between attribution labels and hex colors
                colors[str(att_rounded)] = rgb.hex
                ii = ff

            to_render = {
                'text': ''.join([t['token'] for t in text_field_heatmap]),
                'ents': ents,
            }

            if attribution_labels:
                template = """
                <mark class="entity" style="background: {bg}; padding: 0.45em 0.6em; margin: 0 0.25em; line-height: 2; 
                border-radius: 0.35em; box-decoration-break: clone; -webkit-box-decoration-break: clone">
                    {text}
                    <span style="font-size: 0.8em; font-weight: bold; line-height: 1; border-radius: 0.35em; text-transform: 
                    uppercase; vertical-align: middle; margin-left: 0.5rem">{label}</span>
                </mark>
                """
            else:
                template = """
                <mark class="entity" style="background: {bg}; padding: 0.15em 0.3em; margin: 0 0.2em; line-height: 2.2;
                border-radius: 0.25em; box-decoration-break: clone; -webkit-box-decoration-break: clone">
                    {text}
                </mark>
                """

            html = displacy.render(
                to_render,
                style='ent',
                manual=True,
                jupyter=jupyter,
                options={'template': template,
                         'colors': colors,
                         }
            )
            if jupyter:
                html = displacy.render(
                    to_render,
                    style='ent',
                    manual=True,
                    jupyter=False,
                    options={'template': template,
                             'colors': colors,
                             }
                )
            full_html += html
        return full_html if not jupyter else None


def avg_attribution_stat(thermostat_dataset: Dataset) -> List:
    """ Given a Thermostat dataset, calculate the average attribution for each token across the whole dataset """
    model_id = get_coordinate(thermostat_dataset, coordinate='Model')
    tokenizer = AutoTokenizer.from_pretrained(model_id)

    token_atts = defaultdict(list)
    for row in thermostat_dataset:
        for input_id, attribution_score in zip(row['input_ids'], row['attributions']):
            # Distinguish between the labels
            if row['label'] == 0:
                # Add the negative attribution score for label 0
                # to the list of attribution scores of a single token
                token_atts[tokenizer.decode(input_id)].append(-attribution_score)
            else:
                token_atts[tokenizer.decode(input_id)].append(attribution_score)

    avgs = defaultdict(float)
    # Calculate the average attribution score from the list of attribution scores of each token
    for token, scores in token_atts.items():
        avgs[token] = sum(scores)/len(scores)
    return sorted(avgs.items(), key=lambda x: x[1], reverse=True)


def explainer_agreement_stat(thermostat_datasets: List) -> List:
    """ Calculate agreement on token attribution scores between multiple Thermostat datasets/explainers """
    assert len(thermostat_datasets) > 1
    all_explainers_atts = {}
    for td in thermostat_datasets:
        assert type(td) == Dataset
        explainer_id = get_coordinate(td, coordinate='Explainer')
        # Add all attribution scores to a dictionary with the key being the name of the explainer
        all_explainers_atts[explainer_id] = td['attributions']

    model_id = get_coordinate(thermostat_datasets[0], coordinate='Model')
    tokenizer = AutoTokenizer.from_pretrained(model_id)

    # Dissimilarity dict for tokens and their contexts
    tokens_dissim = {}
    for row in zip(thermostat_datasets[0]['input_ids'],
                   *list(all_explainers_atts.values())):
        # Decode all tokens of one data point
        tokens = tokenizer.decode(list(row)[0], skip_special_tokens=True)
        for idx, input_id in enumerate(zip(*list(row))):
            if list(input_id)[0] in tokenizer.all_special_ids:
                continue

            att_explainers = list(input_id)[1:]
            max_att = max(att_explainers)
            min_att = min(att_explainers)

            # Key: All tokens (context), single token in question, index of token in context
            tokens_dissim[(tokenizer.decode(list(input_id)[0]), tokens, idx)]\
                = {'dissim': max_att - min_att,  # Maximum difference in attribution
                   'atts': dict(zip(all_explainers_atts.keys(), att_explainers))}
    return sorted(tokens_dissim.items(), key=lambda x: x[1]['dissim'], reverse=True)
