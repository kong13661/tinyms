# Copyright 2021 Huawei Technologies Co., Ltd
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
# http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
# ============================================================================

from mindspore.nn import loss
from mindspore.nn.loss import *
from mindspore.nn.loss.loss import _Loss
from tinyms import Tensor, dtype
import tinyms.primitives as P


__all__ = []
__all__.extend(loss.__all__)


class CrossEntropyWithLabelSmooth(_Loss):
    """
    CrossEntropyWith LabelSmooth.

    Args:
        smooth_factor (float): smooth factor. Default is 0.
        num_classes (int): number of classes. Default is 1000.

    Returns:
        None.

    Examples:
        >>> CrossEntropyWithLabelSmooth(smooth_factor=0., num_classes=1000)
    """

    def __init__(self, smooth_factor=0., num_classes=1000):
        super(CrossEntropyWithLabelSmooth, self).__init__()
        self.onehot = P.OneHot()
        self.on_value = Tensor(1.0 - smooth_factor, dtype.float32)
        self.off_value = Tensor(1.0 * smooth_factor /
                                (num_classes - 1), dtype.float32)
        self.ce = P.SoftmaxCrossEntropyWithLogits()
        self.mean = P.ReduceMean(False)
        self.cast = P.Cast()

    def construct(self, logit, label):
        one_hot_label = self.onehot(self.cast(label, dtype.int32), P.shape(logit)[1],
                                    self.on_value, self.off_value)
        out_loss = self.ce(logit, one_hot_label)
        out_loss = self.mean(out_loss, 0)
        return out_loss


class BCEWithLogits(_Loss):
    """
    BCEWithLogits creates a criterion to measure the Binary Cross Entropy between the true labels and
    predicted labels with sigmoid logits.

    Args:
        reduction (str): Specifies the reduction to be applied to the output.
            Its value must be one of 'none', 'mean', 'sum'. Default: 'none'.

    Outputs:
        Tensor or Scalar, if `reduction` is 'none', then output is a tensor and has the same shape as `inputs`.
        Otherwise, the output is a scalar.
    """
    def __init__(self, reduction='mean'):
        super(BCEWithLogits, self).__init__()
        if reduction is None:
            reduction = 'none'
        if reduction not in ('mean', 'sum', 'none'):
            raise ValueError(f"reduction method for {reduction.lower()} is not supported")

        self.loss = P.SigmoidCrossEntropyWithLogits()
        self.reduce = False
        if reduction == 'sum':
            self.reduce_mode = P.ReduceSum()
            self.reduce = True
        elif reduction == 'mean':
            self.reduce_mode = P.ReduceMean()
            self.reduce = True

    def construct(self, predict, target):
        loss = self.loss(predict, target)
        if self.reduce:
            loss = self.reduce_mode(loss)
        return loss


class GANLoss(_Loss):
    """
    Cycle GAN loss factory.

    Args:
        mode (str): The type of GAN objective. It currently supports 'vanilla', 'lsgan'. Default: 'lsgan'.
        reduction (str): Specifies the reduction to be applied to the output.
            Its value must be one of 'none', 'mean', 'sum'. Default: 'none'.

    Outputs:
        Tensor or Scalar, if `reduction` is 'none', then output is a tensor and has the same shape as `inputs`.
        Otherwise, the output is a scalar.
    """
    def __init__(self, mode="lsgan", reduction='mean'):
        super(GANLoss, self).__init__()
        self.loss = None
        self.ones = P.OnesLike()
        if mode == "lsgan":
            self.loss = loss.MSELoss(reduction)
        elif mode == "vanilla":
            self.loss = BCEWithLogits(reduction)
        else:
            raise NotImplementedError(f'GANLoss {mode} not recognized, we support lsgan and vanilla.')

    def construct(self, predict, target):
        target = P.cast(target, P.dtype(predict))
        target = self.ones(predict) * target
        loss = self.loss(predict, target)
        return loss


class GeneratorLoss(_Loss):
    """
    Cycle GAN generator loss.

    Args:
        args (class): Option class.
        generator (Cell): Generator of CycleGAN.
        D_A (Cell): The discriminator network of domain A to domain B.
        D_B (Cell): The discriminator network of domain B to domain A.

    Outputs:
        Tuple Tensor, the losses of generator.
    """
    def __init__(self, generator, D_A, D_B):
        super(GeneratorLoss, self).__init__()
        self.lambda_A = 10.0
        self.lambda_B = 10.0
        self.lambda_idt = 0.5
        self.use_identity = True
        self.dis_loss = GANLoss("lsgan")
        self.rec_loss = loss.L1Loss("mean")
        self.generator = generator
        self.D_A = D_A
        self.D_B = D_B
        self.true = Tensor(True, dtype.bool_)

    def construct(self, img_A, img_B):
        """If use_identity, identity loss will be used."""
        fake_A, fake_B, rec_A, rec_B, identity_A, identity_B = self.generator(img_A, img_B)
        loss_G_A = self.dis_loss(self.D_B(fake_B), self.true)
        loss_G_B = self.dis_loss(self.D_A(fake_A), self.true)
        loss_C_A = self.rec_loss(rec_A, img_A) * self.lambda_A
        loss_C_B = self.rec_loss(rec_B, img_B) * self.lambda_B
        if self.use_identity:
            loss_idt_A = self.rec_loss(identity_A, img_A) * self.lambda_A * self.lambda_idt
            loss_idt_B = self.rec_loss(identity_B, img_B) * self.lambda_B * self.lambda_idt
        else:
            loss_idt_A = 0
            loss_idt_B = 0
        loss_G = loss_G_A + loss_G_B + loss_C_A + loss_C_B + loss_idt_A + loss_idt_B
        return (fake_A, fake_B, loss_G, loss_G_A, loss_G_B, loss_C_A, loss_C_B, loss_idt_A, loss_idt_B)


class DiscriminatorLoss(_Loss):
    """
    Cycle GAN discriminator loss.

    Args:
        args (class): option class.
        D_A (Cell): The discriminator network of domain A to domain B.
        D_B (Cell): The discriminator network of domain B to domain A.

    Outputs:
        Tuple Tensor, the loss of discriminator.
    """
    def __init__(self, D_A, D_B, reduction='none'):
        super(DiscriminatorLoss, self).__init__()
        self.D_A = D_A
        self.D_B = D_B
        self.false = Tensor(False, dtype.bool_)
        self.true = Tensor(True, dtype.bool_)
        self.dis_loss = GANLoss("lsgan")
        self.rec_loss = loss.L1Loss("mean")
        self.reduction = reduction

    def construct(self, img_A, img_B, fake_A, fake_B):
        D_fake_A = self.D_A(fake_A)
        D_img_A = self.D_A(img_A)
        D_fake_B = self.D_B(fake_B)
        D_img_B = self.D_B(img_B)
        loss_D_A = self.dis_loss(D_fake_A, self.false) + self.dis_loss(D_img_A, self.true)
        loss_D_B = self.dis_loss(D_fake_B, self.false) + self.dis_loss(D_img_B, self.true)
        loss_D = (loss_D_A + loss_D_B) * 0.5
        return loss_D
