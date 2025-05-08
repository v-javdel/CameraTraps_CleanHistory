# Copyright (c) Microsoft Corporation. All rights reserved.
# Licensed under the MIT License.

import numpy as np
from PIL import Image
from tqdm import tqdm
from collections import OrderedDict

import torch
import torch.nn as nn
from torchvision.models.resnet import BasicBlock, Bottleneck, ResNet
from torch.hub import load_state_dict_from_url
from torch.utils.data import DataLoader

from ..base_classifier import BaseClassifierInference 
from ....data import transforms as pw_trans
from ....data import datasets as pw_data 

# Making the PlainResNetInference class available for import from this module
__all__ = ["PlainResNetInference"]


class ResNetBackbone(ResNet):
    """
    Custom ResNet Backbone that extracts features from input images.
    """
    def _forward_impl(self, x):
        # Following the ResNet structure to extract features
        x = self.conv1(x)
        x = self.bn1(x)
        x = self.relu(x)
        x = self.maxpool(x)

        x = self.layer1(x)
        x = self.layer2(x)
        x = self.layer3(x)
        x = self.layer4(x)

        x = self.avgpool(x)
        x = torch.flatten(x, 1)
        return x


class PlainResNetClassifier(nn.Module):
    """
    Basic ResNet Classifier that uses a custom ResNet backbone.
    """
    name = "PlainResNetClassifier"

    def __init__(self, num_cls=1, num_layers=50):
        super(PlainResNetClassifier, self).__init__()
        self.num_cls = num_cls
        self.num_layers = num_layers
        self.feature = None
        self.classifier = None
        self.criterion_cls = None
        # Initialize the network and weights
        self.setup_net()

    def setup_net(self):
        """
        Set up the ResNet classifier according to the specified number of layers.
        """
        kwargs = {}

        if self.num_layers == 18:
            block = BasicBlock
            layers = [2, 2, 2, 2]
            # ... [Missing weight URL definition for ResNet18]
        elif self.num_layers == 50:
            block = Bottleneck
            layers = [3, 4, 6, 3]
            # ... [Missing weight URL definition for ResNet50]
        else:
            raise Exception("ResNet Type not supported.")

        self.feature = ResNetBackbone(block, layers, **kwargs)
        self.classifier = nn.Linear(512 * block.expansion, self.num_cls)

    def setup_criteria(self):
        """
        Setup the criterion for classification.
        """
        self.criterion_cls = nn.CrossEntropyLoss()

    def feat_init(self):
        """
        Initialize the features using pretrained weights.
        """
        init_weights = self.pretrained_weights.get_state_dict(progress=True)
        init_weights = OrderedDict({k.replace("module.", "").replace("feature.", ""): init_weights[k]
                                    for k in init_weights})
        self.feature.load_state_dict(init_weights, strict=False)
        # Print missing and unused keys for debugging purposes
        load_keys = set(init_weights.keys())
        self_keys = set(self.feature.state_dict().keys())
        missing_keys = self_keys - load_keys
        unused_keys = load_keys - self_keys
        print("missing keys:", sorted(list(missing_keys)))
        print("unused_keys:", sorted(list(unused_keys)))


class PlainResNetInference(BaseClassifierInference):
    """
    Inference module for the PlainResNet Classifier.
    """
    IMAGE_SIZE = None
    def __init__(self, num_cls=36, num_layers=50, weights=None, device="cpu", url=None, transform=None):
        super(PlainResNetInference, self).__init__()
        self.device = device
        self.net = PlainResNetClassifier(num_cls=num_cls, num_layers=num_layers)
        if weights:
            clf_weights = torch.load(weights, map_location=torch.device(self.device))
        elif url:
            clf_weights = load_state_dict_from_url(url, map_location=torch.device(self.device))
        else:
            raise Exception("Need weights for inference.")
        self.load_state_dict(clf_weights["state_dict"], strict=True)
        self.eval()
        self.net.to(self.device)

        if transform:
            self.transform = transform
        else:
            self.transform = pw_trans.Classification_Inference_Transform(target_size=self.IMAGE_SIZE)

    def results_generation(self, logits: torch.Tensor, img_ids: list[str], id_strip: str = None) -> list[dict]:
        """
        Process logits to produce final results. 

        Args:
            logits (torch.Tensor): Logits from the network.
            img_ids (list[str]): List of image paths.
            id_strip (str): Stripping string for better image ID saving.       

        Returns:
            list[dict]: List of dictionaries containing the results.
        """
        pass

    def forward(self, img):
        feats = self.net.feature(img)
        logits = self.net.classifier(feats)
        return logits

    def single_image_classification(self, img, img_id=None, id_strip=None):
        if type(img) == str:
            img = Image.open(img)
        else:
            img = Image.fromarray(img)
        img = self.transform(img)
        logits = self.forward(img.unsqueeze(0).to(self.device))
        return self.results_generation(logits.cpu(), [img_id], id_strip=id_strip)[0]

    def batch_image_classification(self, data_path=None, det_results=None, id_strip=None):
        """
        Process a batch of images for classification.
        """

        if data_path:
            dataset = pw_data.ImageFolder(
                data_path,
                transform=self.transform,
                path_head='.'
            )
        elif det_results:
            dataset = pw_data.DetectionCrops(
                det_results,
                transform=self.transform,
                path_head='.'
            )
        else:
            raise Exception("Need data for inference.")

        dataloader = DataLoader(dataset, batch_size=32, shuffle=False, 
                                pin_memory=True, num_workers=4, drop_last=False)
        total_logits = []
        total_paths = []

        with tqdm(total=len(dataloader)) as pbar: 
            for batch in dataloader:
                imgs, paths = batch
                imgs = imgs.to(self.device)
                total_logits.append(self.forward(imgs))
                total_paths.append(paths)
                pbar.update(1)

        total_logits = torch.cat(total_logits, dim=0).cpu()
        total_paths = np.concatenate(total_paths, axis=0)

        return self.results_generation(total_logits, total_paths, id_strip=id_strip)
