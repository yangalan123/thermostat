{
    "path": "$HOME/experiments/thermostat",
    "device": "cuda",
    "dataset": {
        "name": "multi_nli",
        "text_field": ["premise", "hypothesis"],
        "split": "validation_matched",
        "end": 2000,
        "columns": ['input_ids', 'attention_mask', 'token_type_ids', 'special_tokens_mask', 'labels'],
        "batch_size": 1,
        "root_dir": "$HOME/experiments/thermostat/datasets",
    },
    "explainer": {
        "name": "ShapleyValueSampling",
        "internal_batch_size": 1,
        "n_samples": 25,
        "early_stopping": -1,
    },
    "model": {
        "name": "textattack/bert-base-uncased-MNLI",
        "mode_load": "hf",
        "path_model": null,
        "tokenization": {
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
