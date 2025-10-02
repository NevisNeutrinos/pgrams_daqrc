import json
import datamon as dm
import numpy as np


class ConfigManager:
    def __init__(self):
        self.config = dm.TpcConfig()
        self.config_dict = self.config.get_metric_dict()
        print("Initialized ConfigManager", self.config_dict)

    def load_json(self, filepath):
        with open(filepath, 'r') as f:
            data = json.load(f)
        # self.config_dict.update(data)
        self.update_from_dict(new_dict=data)
        self.config.set_config_dict(self.config_dict)

    def check_if_sequence(self, key, value):
        should_be_seq = type(self.config_dict[key]) is list or type(self.config_dict[key]) is np.ndarray
        new_config_is_seq = type(value) is list or type(value) is np.ndarray
        return should_be_seq and not new_config_is_seq

    def update_from_dict(self, new_dict):
        for outer_key in new_dict.keys():
            for key, value in new_dict[outer_key].items():
                if key in self.config_dict:
                    if self.check_if_sequence(key, value):
                        self.config_dict[key] = [value] * len(self.config_dict[key])
                    else:
                        self.config_dict[key] = value
        self.config.set_config_dict(self.config_dict)

    def serialize(self):
        return self.config.serialize()

    def get_config(self):
        return self.config_dict