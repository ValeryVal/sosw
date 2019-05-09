__all__ = ['EcologyManager', 'ECO_STATUSES']
__author__ = "Nikolay Grishchenko"
__version__ = "1.0"

import boto3
import json
import logging
import operator
import os
import random
import time

from collections import defaultdict
from collections import OrderedDict
from typing import Dict, List, Optional, Union

from sosw.app import Processor
from sosw.labourer import Labourer
from sosw.components.benchmark import benchmark
from sosw.components.helpers import make_hash
from sosw.managers.task import TaskManager


logger = logging.getLogger()
logger.setLevel(logging.INFO)

ECO_STATUSES = (
    (0, 'Bad'),
    (1, 'Poor'),
    (2, 'Moderate'),
    (3, 'Good'),
    (4, 'High'),
)


class EcologyManager(Processor):
    DEFAULT_CONFIG = {
    }

    running_tasks = defaultdict(int)
    health_metrics: Dict = None
    task_client: TaskManager = None  # Will be Circular import! Careful!
    cloudwatch_client: boto3.client = None


    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)


    def __call__(self, event):
        raise NotImplementedError


    def register_task_manager(self, task_manager: TaskManager):
        """
        We will have to make some queries, and don't want to initialise another TaskManager locally.
        Just receive the pointer to TaskManager from whoever needs.

        This could be in __init__, but I don't want to update the initialization workflows for every function
        initialising me. They usually use built-in in core Processor mechanism to register_clients().
        """

        logger.info("Registering TaskManager for EcologyManager")
        self.task_client = task_manager

        logger.info("Reset cache of running_tasks counter in EcologyManager")
        self.running_tasks = defaultdict(int)

        logger.info("Reset cache of health_metrics in EcologyManager")
        self.health_metrics = dict()


    @property
    def eco_statuses(self):
        return [x[0] for x in ECO_STATUSES]


    def fetch_metric_stats(self, **kwargs):

        result = self.cloudwatch_client.get_metric_statistics(**kwargs)

        return result


    def get_labourer_status(self, labourer: Labourer) -> int:
        """
        Get the worst (lowest) health status according to preconfigured health metrics of the Labourer.

        .. _ECO_STATUSES:

        Current ECO_STATUSES:

        - (0, 'Bad')
        - (1, 'Poor')
        - (2, 'Moderate')
        - (3, 'Good')
        - (4, 'High')
        """

        health = max(map(lambda x: x[0], ECO_STATUSES))

        for health_metric in getattr(labourer, 'health_metrics', dict()).values():

            metric_hash = make_hash(health_metric['details'])
            if metric_hash not in self.health_metrics:
                self.health_metrics[metric_hash] = self.fetch_metric_stats(**health_metric['details'])
                logger.info(f"Updated the cache of Ecology metric {metric_hash} - {health_metric} "
                            f"with {self.health_metrics[metric_hash]}")

            value = self.health_metrics[metric_hash]
            logger.debug(f"Ecology metric {metric_hash} has {value}")

            health = min(health, self.get_health(value, metric=health_metric))

        logger.info(f"Ecology health of Labourer {labourer} is {health}")

        return health


    def get_health(self, value: Union[int, float], metric: Dict) -> int:
        """
        Checks the value against the health_metric configuration.
        """

        op = getattr(operator, metric.get('feeling_comparison_operator'))

        # Find the first configured feeling from the map that does not comply.
        # Order and validate the feelings
        feelings = OrderedDict([(key, metric['feelings'][key])
                                for key in sorted(metric['feelings'].keys(), reverse=True)])

        last_target = 0
        for health, target in feelings.items():
            if op(target, last_target):
                raise ValueError(f"Order of values if feelings is invalid and doesn't match expected eco statuses: "
                                 f"{feelings.items()}. Failed: {last_target} not "
                                 f"{metric.get('feeling_comparison_operator')} {target}")

            if op(value, target):
                return health

            last_target = target

        return 0


    def count_running_tasks_for_labourer(self, labourer: Labourer) -> int:
        """
        TODO Refactor this to cache the value in the Labourer object itself.
        Should also update add_running_tasks_for_labourer() for that.
        """

        if not self.task_client:
            raise RuntimeError("EcologyManager doesn't have a TaskManager registered. "
                               "You have to call register_task_manager() after initiazation and pass the pointer "
                               "to your TaskManager instance.")

        if labourer.id not in self.running_tasks.keys():
            self.running_tasks[labourer.id] = self.task_client.get_count_of_running_tasks_for_labourer(labourer)
            logger.debug(f"EcologyManager.count_running_tasks_for_labourer() recalculated cache for Labourer "
                         f"{labourer}")

        logger.debug(f"EcologyManager.count_running_tasks_for_labourer() returns: {self.running_tasks[labourer.id]}")
        return self.running_tasks[labourer.id]


    def add_running_tasks_for_labourer(self, labourer: Labourer, count: int = 1):
        """
        Adds to the current counter of running tasks the given `count`.
        Invokes the getter first in case the original number was not yet calculated from DynamoDB.
        """

        self.running_tasks[labourer.id] = self.count_running_tasks_for_labourer(labourer) + count


    def get_labourer_average_duration(self, labourer: Labourer) -> int:
        """
        Calculates the average duration of `labourer` executions.

        The operation consumes DynamoDB RCU . Normally this method is called for each labourer only once during
        registration of Labourers. If you want to learn this value, you should ask Labourer object.

        .. code-block::python

           some_labourer.get_attr('average_duration')
        """

        if not self.task_client:
            raise RuntimeError("EcologyManager doesn't have a TaskManager registered. "
                               "You have to call register_task_manager() after initiazation and pass the pointer "
                               "to your TaskManager instance.")

        return self.task_client.get_average_labourer_duration(labourer)


    def get_max_labourer_duration(self, labourer: Labourer) -> int:
        """
        Maximum duration of `labourer` executions.
        Should ask this from aws:lambda API, but at the moment use the hardcoded maximum.
        # TODO implement me.
        """

        return 900


    # The task_client of EcologyManager is just a pointer. We skip recursive stats to avoid infinite loop.
    def get_stats(self, recursive=False):
        return super().get_stats(recursive=False)


    def reset_stats(self, recursive=False):
        return super().reset_stats(recursive=False)
