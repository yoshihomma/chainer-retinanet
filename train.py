import argparse
import multiprocessing

import chainer
from chainer import serializers
from chainer import training

from configs import cfg
from utils.path import get_outdir, get_logdir
from extensions import LogTensorboard
from setup_helpers import setup_dataset
from setup_helpers import setup_model, setup_train_chain, freeze_params
from setup_helpers import setup_optimizer, add_hock_optimizer


def converter(batch, device=None):
    # do not send data to gpu (device is ignored)
    return tuple(list(v) for v in zip(*batch))


def parse_args():
    parser = argparse.ArgumentParser()
    parser.add_argument('config', type=str,
                        help='Path to the config file.')
    parser.add_argument('--tensorboard', type=bool, default=True,
                        help='Whether use Tensorboard. Default is True.')
    parser.add_argument('--resume', type=str)
    args = parser.parse_args()
    return args


def main():
    args = parse_args()
    cfg.merge_from_file(args.config)
    cfg.freeze()

    if hasattr(multiprocessing, 'set_start_method'):
        multiprocessing.set_start_method('forkserver')
        p = multiprocessing.Process()
        p.start()
        p.join()

    model = setup_model(cfg)
    train_chain = setup_train_chain(cfg, model)
    train_chain.to_gpu(0)

    train_dataset = setup_dataset(cfg, 'train')
    train_iter = chainer.iterators.MultithreadIterator(
        train_dataset, cfg.n_sample_per_gpu)
    # optimizer = chainermn.create_multi_node_optimizer(
    #     setup_optimizer(cfg), comm)
    optimizer = setup_optimizer(cfg)
    optimizer.setup(train_chain)
    add_hock_optimizer(optimizer, cfg)
    train_chain = freeze_params(cfg, train_chain)

    updater = training.updaters.StandardUpdater(
        train_iter, optimizer, converter=converter)
    trainer = training.Trainer(
        updater, (cfg.solver.n_iteration, 'iteration'),
        get_outdir(args.config))

    # extention
    log_interval = 10, 'iteration'
    trainer.extend(training.extensions.LogReport(trigger=log_interval))
    trainer.extend(training.extensions.observe_lr(), trigger=log_interval)
    trainer.extend(training.extensions.PrintReport(
        ['epoch', 'iteration', 'lr', 'main/loss',
            'main/loss/loc', 'main/loss/conf',
         ]),
        trigger=log_interval)
    trainer.extend(training.extensions.ProgressBar(update_interval=10))

    trainer.extend(training.extensions.snapshot(),
                   trigger=(10000, 'iteration'))
    trainer.extend(
        training.extensions.snapshot_object(
            model, 'model_iter_{.updater.iteration}'),
        trigger=(cfg.solver.n_iteration, 'iteration'))
    if args.tensorboard:
        trainer.extend(LogTensorboard(
            ['lr', 'main/loss', 'main/loss/loc', 'main/loss/conf'],
            trigger=(10, 'iteration'), log_dir=get_logdir(args.config)))

    if len(cfg.solver.lr_step):
        trainer.extend(training.extensions.MultistepShift(
            'lr', 0.1, cfg.solver.lr_step, cfg.solver.base_lr, optimizer))

    if args.resume:
        serializers.load_npz(args.resume, trainer, strict=False)

    trainer.run()


if __name__ == '__main__':
    main()
