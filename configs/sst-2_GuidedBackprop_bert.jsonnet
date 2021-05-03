{
    "path": "$HOME/experiments/thermostat",
    "device": "cuda",
    "dataset": {
        "name": "glue",
        "subset": "sst2",
        "split": "test",
        "text_field": "sentence",
        "columns": ['input_ids', 'attention_mask', 'token_type_ids', 'labels'],
        "batch_size": 1,
        "root_dir": "$HOME/experiments/thermostat/datasets",
    },
    "explainer": {
        "name": "GuidedBackprop",
        "internal_batch_size": 1,
    },
    "model": {
        "name": "textattack/bert-base-uncased-SST-2",
        "mode_load": "hf",
        "path_model": null,
        "tokenizer": {
            "max_length": 512,
            "padding": "max_length",
            "return_tensors": "np",
            "truncation": true,
            "special_tokens_mask": true,
        }
    },
    "visualization": {
        "columns": ["attributions", "predictions", "input_ids", "labels"],
        "gamma": 2.0,
        "normalize": true,
    }
}