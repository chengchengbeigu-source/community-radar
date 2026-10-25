# -*- coding: utf-8 -*-
"""数据源注册表：collect.py 只认这里，加新源 = 加一行 import + 注册。"""
from . import nodeloc, nodeseek, x

REGISTRY = {"nodeseek": nodeseek, "nodeloc": nodeloc, "x": x}
