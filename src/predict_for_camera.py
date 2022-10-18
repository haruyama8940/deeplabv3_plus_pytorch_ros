#!/usr/bin/env python3
# codding = utf-8

# Python imports
from re import S
import numpy as np
import cv2
import os

# ROS imports
import rospy

# Deep Learning imports
import torch
import torch.nn as nn
from torchvision import transforms as T
from cv_bridge import CvBridge, CvBridgeError
from sensor_msgs.msg import Image
from std_msgs.msg import Float32
from std_srvs.srv import SetBool ,SetBoolRequest ,SetBoolResponse
import network
import utils
from datasets import VOCSegmentation, Cityscapes, cityscapes


class image_segmentation:
    def __init__(self):
        # Name of training set
        self.dataset = rospy.get_param('~dataset', 'cityscapes')
        rospy.loginfo("Use Dataset name %s", self.dataset)

        # Deeplab Options
        available_models = sorted(name for name in network.modeling.__dict__ if name.islower() and \
                                  not (name.startswith("__") or name.startswith('_')) and callable(
            network.modeling.__dict__[name])
                                  )
        # Model name
        self.model_mode = rospy.get_param('~model', 'deeplabv3plus_mobilenet')
        rospy.loginfo("Use Model name %s", self.model_mode)

        # Apply separable conv to decoder and aspp
        self.separable_conv = rospy.get_param('~separable_conv', False)
        rospy.loginfo("Separable conv to decoder and aspp: %s" % self.separable_conv)

        # Output stride
        self.output_stride = rospy.get_param('output_stride', 16)
        rospy.loginfo("Output stride: %s" % self.output_stride)

        # Train options
        # Resume from checkpoint(weights)
        self.ckpt = rospy.get_param('~ckpt')
        rospy.loginfo("Checkpoint path %s" % self.ckpt)

        self.save_val_results_to = None
        self.crop_val = False
        self.val_batch_size = 4
        self.crop_size = 513

        # Gpu ID
        # self.gpu_id = rospy.get_param('~gpu_id', 0)
        # rospy.loginfo("Gpu ID %s " % self.gpu_id)

        # Ros options
        self.image_pub_topic = rospy.get_param('~image_publish_topic', 'segmentation_image')
        self.image_pub = rospy.Publisher(self.image_pub_topic, Image, queue_size=1)

        self.bridge = CvBridge()

        self.image_sub_topic = rospy.get_param('~image_subscribe_topic', '/camera/image_raw')
        self.image_sub = rospy.Subscriber(self.image_sub_topic, Image, self.segmentation)

        #change topics
        self.alpha_topic = rospy.get_param("~alpha_topic",'/alpha')
        self.threshold_sub =rospy.Subscriber(self.alpha_topic,Float32,self.thresholdCallback)
        self.threshold_num = rospy.get_param('threshold_num',0.0)
        rospy.loginfo("threshold num :%s" % self.threshold_num)

        self.change_flag_srv = rospy.Service('/switch_segmentation',SetBool,self.callback_change_mode)
        self.change_threshold_flag = False
        self.change_mode_flag = False

        # Predict options
        if self.dataset.lower() == 'voc':
            self.num_classes = 21
            self.decode_fn = VOCSegmentation.decode_target
        elif self.dataset.lower() == 'cityscapes':
            self.num_classes = 19
            self.decode_fn = Cityscapes.decode_target

        # os.environ['CUDA_VISIBLE_DEVICES'] = self.gpu_id
        self.device = torch.device('cuda' if torch.cuda.is_available() else 'cpu')
        print("Device: %s" % self.device)

        # Set up model (all models are 'constructed at network.modeling)
        self.model = network.modeling.__dict__[self.model_mode](num_classes=self.num_classes,
                                                                output_stride=self.output_stride)
        if self.separable_conv and 'plus' in self.model_mode:
            network.convert_to_separable_conv(self.model.classifier)
        utils.set_bn_momentum(self.model.backbone, momentum=0.01)

        if self.ckpt is not None and os.path.isfile(self.ckpt):
            # https://github.com/VainF/DeepLabV3Plus-Pytorch/issues/8#issuecomment-605601402, @PytaichukBohdan
            checkpoint = torch.load(self.ckpt, map_location=torch.device('cpu'))
            self.model.load_state_dict(checkpoint["model_state"])
            model = nn.DataParallel(self.model)
            model.to(self.device)
            print("Resume model from %s" % self.ckpt)
            del checkpoint
        else:
            print("[!] Retrain")
            self.model = nn.DataParallel(self.model)
            self.model.to(self.device)
        with torch.no_grad():
            self.model = self.model.eval()

    def thresholdCallback(self,data):
        if data.data <= self.threshold_num:
            self.change_threshold_flag = True
        else:
            self.change_threshold_flag = False
    
    def callback_change_mode(self, data):
        resp = SetBoolResponse()
        self.change_mode_flag = data.data
        resp.message = "change_mode: " + str(self.learning)
        resp.success = True
        return resp
    
    def segmentation(self, data):
        if self.change_mode_flag and self.change_threshold_flag:
            print(self.change_mode_flag)
            if self.crop_val:
                T.Compose([
                    T.Resize(self.crop_size),
                    T.CenterCrop(self.crop_size),
                    T.ToTensor(),
                    T.Normalize(mean=[0.485, 0.456, 0.406],
                                std=[0.229, 0.224, 0.225]),
                ])
            else:
                transform = T.Compose([
                    T.ToTensor(),
                    T.Normalize(mean=[0.485, 0.456, 0.406],
                                std=[0.229, 0.224, 0.225]),
                ])

            try:
                cv_image = self.bridge.imgmsg_to_cv2(data, "bgr8")
            except CvBridgeError as e:
                print(e)

            cv_image = transform(cv_image).unsqueeze(0).to(self.device)
            pred = self.model(cv_image)
            pred = pred.max(1)[1].cpu().numpy()[0]
            colorized_preds = self.decode_fn(pred).astype('uint8')
            colorized_preds = cv2.cvtColor(np.asarray(colorized_preds), cv2.COLOR_RGB2BGR)
            try:
                self.image_pub.publish(self.bridge.cv2_to_imgmsg(colorized_preds, "bgr8"))
            except CvBridgeError as e:
                print(e)
        else:
            pass


if __name__ == '__main__':
    try:
        rospy.init_node("segmentation")
        rospy.loginfo("Satarting")
        image_segmentation()
        rospy.spin()
    except KeyboardInterrupt:
        print("shutting down")
