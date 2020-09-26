import logging
import os
import time
from argparse import ArgumentParser
from itertools import product

import torch
from pytorch_lightning import Trainer, seed_everything
from pytorch_lightning.callbacks import ModelCheckpoint, EarlyStopping
from pytorch_lightning.loggers import TensorBoardLogger

from datasets import available_datasets, GraphDataModule
from models import NodeClassifier
from privacy import available_mechanisms
from transforms import Privatize
from utils import TermColors


def train_and_test(dataset, method, eps, K, aggregator, args, repeats, output_dir):
    for run in range(repeats):
        params = {
            'task': 'node',
            'dataset': dataset.name,
            'method': method,
            'eps': eps,
            'steps': K,
            'aggr': aggregator,
            'run': run
        }

        params_str = ' | '.join([f'{key}={val}' for key, val in params.items()])
        print(TermColors.FG.green + params_str + TermColors.reset)

        save_dir = os.path.join(output_dir, 'node', dataset.name, method, str(eps), str(K), aggregator)
        logger = TensorBoardLogger(save_dir=save_dir, name=None)

        checkpoint_path = os.path.join('checkpoints', save_dir)
        checkpoint_callback = ModelCheckpoint(monitor='val_loss', filepath=checkpoint_path)

        params = vars(args)
        log_learning_curve = run == 0 and (method == 'raw' or method == 'mbm')
        model = NodeClassifier(aggregator=aggregator, K=K, log_learning_curve=log_learning_curve, **params)

        trainer = Trainer.from_argparse_args(
            args=args,
            precision=32,
            gpus=int(args.device == 'cuda' and torch.cuda.is_available()),
            max_epochs=1000,
            checkpoint_callback=checkpoint_callback,
            early_stop_callback=EarlyStopping(patience=200),
            logger=logger,
            log_save_interval=500,
            weights_summary=None,
            deterministic=True,
            progress_bar_refresh_rate=10,
        )

        privatize = Privatize(method=method, eps=eps)
        dataset.add_transform(privatize)
        trainer.fit(model=model, datamodule=dataset)
        trainer.test(datamodule=dataset, ckpt_path='best', verbose=True)


def batch_train_and_test(args):
    dataset = GraphDataModule(name=args.dataset, normalize=(0, 1), device=args.device)

    if 'raw' in args.methods:
        configs = list(product(['raw'], [0.0], args.steps, args.aggs))
        configs += list(product(set(args.methods) - {'raw'}, set(args.epsilons), args.steps, args.aggs))
    else:
        configs = list(product(args.methods, args.epsilons, args.steps, args.aggs))

    for method, eps, steps, aggr in configs:
        train_and_test(
            dataset=dataset,
            method=method,
            eps=eps,
            K=steps,
            aggregator=aggr,
            args=args,
            repeats=args.repeats,
            output_dir=args.output_dir
        )


def main():
    seed_everything(12345)
    logging.getLogger("lightning").setLevel(logging.ERROR)
    logging.captureWarnings(True)

    parser = ArgumentParser()
    parser.add_argument('-d', '--dataset', type=str, choices=available_datasets(), required=True)
    parser.add_argument('-m', '--methods', nargs='+', choices=available_mechanisms() + ['raw'], required=True)
    parser.add_argument('-e', '--epsilons', nargs='*', type=float, dest='epsilons', default=[1])
    parser.add_argument('-k', '--steps', nargs='*', type=int, default=[1])
    parser.add_argument('-a', '--aggs', nargs='*', type=str, default=['mean'])
    parser.add_argument('-r', '--repeats', type=int, default=1)
    parser.add_argument('-o', '--output-dir', type=str, default='./results')
    parser.add_argument('--device', type=str, default='cuda', choices=['cpu', 'cuda'])
    parser = NodeClassifier.add_module_specific_args(parser)
    args = parser.parse_args()

    # check if eps > 0 for LDP methods
    if len(set(args.methods) & set(available_mechanisms())) > 0:
        if min(args.epsilons) <= 0:
            parser.error('LDP methods require eps > 0.')

    print(args)
    start = time.time()
    batch_train_and_test(args)
    end = time.time()
    print('\nTotal time spent:', end - start, 'seconds.\n\n')


if __name__ == '__main__':
    main()
