# -*- coding: utf-8 -*-
"""采集器注册表。加新源只需在这里挂一下，config.yaml 里启用即可。"""
from . import linuxdo, linuxsb, nodeloc, nodeseek, x

REGISTRY = {
    "nodeseek": nodeseek,
    "nodeloc": nodeloc,
    "linuxdo": linuxdo,
    "linuxsb": linuxsb,
    "x": x,
}
