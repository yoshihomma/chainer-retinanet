import numpy as np
import chainer
import chainer.functions as F
from chainer import cuda
from utils.bbox import calc_iou


class RetinaNetTrainChain(chainer.Chain):
    _std = (0.1, 0.2)

    def __init__(self, model, loc_loss, conf_loss, fg_thresh=0.5,
                 bg_thresh=0.4):
        super(RetinaNetTrainChain, self).__init__()
        with self.init_scope():
            self.model = model
        self._loc_loss = loc_loss
        self._conf_loss = conf_loss
        self._fg_thresh = fg_thresh
        self._bg_thresh = bg_thresh

    def forward(self, imgs, gt_bboxes, gt_labels):
        anchors, locs, confs, scales = self.model(imgs)
        gt_bboxes = self._scale_gt_bboxes(gt_bboxes, scales)
        anchors, locs, confs, gt_bboxes, gt_labels = self._asign_gt_to_anchor(
            anchors, locs, confs, gt_bboxes, gt_labels)
        gt_locs = self._convert_bbox_to_locs(anchors, gt_bboxes)
        loc_loss, conf_loss = self._calc_losses(
            locs, confs, gt_locs, gt_labels)
        loss = loc_loss + conf_loss
        chainer.reporter.report({
            'loss': loss,
            'loss/loc': loc_loss,
            'loss/conf': conf_loss},
            self)
        return loss

    def _scale_gt_bboxes(self, gt_bboxes, scales):
        for i in range(len(scales)):
            gt_bboxes[i] = gt_bboxes[i] * scales[i]
        return gt_bboxes

    def _asign_gt_to_anchor(self, anchors, locs, confs, gt_bboxes, gt_labels):
        _anchors = []
        _locs = []
        _confs = []
        _gt_labels = []
        _gt_bboxes = []
        for anchor, loc, conf, gt_bbox, gt_label in zip(
                anchors, locs, confs, gt_bboxes, gt_labels):
            iou = calc_iou(anchor, gt_bbox)
            max_iou = self.xp.max(iou, axis=-1)
            print(np.max(max_iou))
            max_iou_indices = self.xp.argmax(iou, axis=-1)
            # label 0 assign bg
            _gt_label = np.array([gt_label[i] + 1 for i in max_iou_indices])
            _gt_bbox = np.array([gt_bbox[i] for i in max_iou_indices])

            fg_mask = max_iou > self._fg_thresh
            bg_mask = max_iou < self._bg_thresh

            _anchors.append(F.vstack((anchor[fg_mask], anchor[bg_mask])))
            _locs.append(F.vstack((loc[fg_mask], loc[bg_mask])))
            _confs.append(F.vstack((conf[fg_mask], conf[bg_mask])))
            _gt_bboxes.append(
                np.vstack((_gt_bbox[fg_mask], np.zeros((np.sum(bg_mask), 4)))))
            _gt_labels.append(
                np.hstack((_gt_label[fg_mask], np.zeros(np.sum(bg_mask)))))

        return _anchors, _locs, _confs, _gt_bboxes, _gt_labels

    # TODO: implement
    def _convert_bbox_to_locs(self, anchors, gt_bboxes):

        locs = []
        for anchor, gt_bbox in zip(anchors, gt_bboxes):
            anchor = anchor.data.copy()
            anchor_yx = (anchor[:, 2:] + anchor[:, :2]) / 2
            anchor_hw = anchor[:, 2:] - anchor[:, :2]
            loc = gt_bbox.copy()

            # tlbr -> yxhw
            loc[:, 2:] -= loc[:, :2]
            loc[:, :2] += loc[:, 2:] / 2
            # offset
            loc[:, :2] = (loc[:, :2] - anchor_yx) / \
                anchor_hw / self._std[0]
            loc[:, 2:] = self.xp.log(
                (loc[:, 2:] + 1e-10) / anchor_hw) / self._std[1]
            locs.append(loc)

        return locs

    def _calc_losses(self, locs, confs, gt_locs, gt_labels):
        batchsize = len(gt_locs)
        gt_locs = [gt_loc.astype(self.xp.float32) for gt_loc in gt_locs]
        gt_labels = [gt_label.astype(self.xp.int32) for gt_label in gt_labels]

        loc_loss = 0
        conf_loss = 0
        for loc, conf, gt_loc, gt_label in zip(
                locs, confs, gt_locs, gt_labels):
            loc_loss = self._loc_loss(loc, gt_loc, gt_label)
            conf_loss += self._conf_loss(conf, gt_label)

        loc_loss /= batchsize
        conf_loss /= batchsize

        return loc_loss, conf_loss
