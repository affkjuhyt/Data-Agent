# Copyright 2020 IBM
# Author: peter.zhong@au1.ibm.com
#
# This is free software; you can redistribute it and/or modify
# it under the terms of the Apache 2.0 License.
#
# This software is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# Apache 2.0 License for more details.
#
# Vendored for the Docling bake-off (DI-02, docs/blueprint/DOCUMENT_INTELLIGENCE_SPEC.md §11) from
# OmniDocBench src/metrics/table_metric.py, commit 7ddb8df411ad9bd2fff139acafe0ed16aa1694fe
# (https://github.com/opendatalab/OmniDocBench, Apache-2.0; see NOTICE in this directory).
# Changed here: the cell text distance is rapidfuzz's Levenshtein (MIT) instead of the
# `Levenshtein` package (GPL-2.0, forbidden by tools/licenses.json) — the same distance, which the
# upstream file already names in a comment; tqdm, the batch helper and the __main__ demo are left
# out; type hints and f-strings; the tokenizer's state is local to a call, not an attribute.
"""TEDS and TEDS-S: tree-edit-distance similarity of a predicted table to its gold, in [0, 1].

1 - APTED(pred, gold) / max(nodes): a node renamed to another tag or span costs 1, a cell with
other text costs its normalised character edit distance, a missing or extra node costs 1.
TEDS-S (`structure_only=True`) ignores the cells' text. Both sides are HTML with a
`<body><table>` (the gold is written by labelstudio/convert.py `table_html`)."""

from __future__ import annotations

from collections import deque
from typing import Any

from apted import APTED, Config
from apted.helpers import Tree
from lxml import etree, html
from rapidfuzz.distance import Levenshtein


class TableTree(Tree):  # type: ignore[misc]
    def __init__(
        self,
        tag: str,
        colspan: int | None = None,
        rowspan: int | None = None,
        content: list[str] | None = None,
        *children: TableTree,
    ) -> None:
        self.tag = tag
        self.colspan = colspan
        self.rowspan = rowspan
        self.content = content
        self.children = list(children)

    def bracket(self) -> str:
        """Show tree using brackets notation"""
        if self.tag == "td":
            result = (
                f'"tag": {self.tag}, "colspan": {self.colspan:d}, '
                f'"rowspan": {self.rowspan:d}, "text": {self.content}'
            )
        else:
            result = f'"tag": {self.tag}'
        for child in self.children:
            result += child.bracket()
        return f"{{{result}}}"


class CustomConfig(Config):  # type: ignore[misc]
    @staticmethod
    def maximum(*sequences: Any) -> int:
        """Get maximum possible value"""
        return max(map(len, sequences))

    def normalized_distance(self, *sequences: Any) -> float:
        """Get distance from 0 to 1"""
        return float(Levenshtein.distance(*sequences)) / self.maximum(*sequences)

    def rename(self, node1: TableTree, node2: TableTree) -> float:
        """Compares attributes of trees"""
        if (
            (node1.tag != node2.tag)
            or (node1.colspan != node2.colspan)
            or (node1.rowspan != node2.rowspan)
        ):
            return 1.0
        if node1.tag == "td" and (node1.content or node2.content):
            return self.normalized_distance(node1.content, node2.content)
        return 0.0


class TEDS:
    """Tree Edit Distance basead Similarity"""

    def __init__(self, structure_only: bool = False, ignore_nodes: list[str] | None = None):
        self.structure_only = structure_only
        self.ignore_nodes = ignore_nodes

    def tokenize(self, node: Any, tokens: list[str]) -> None:
        """Tokenizes table cells"""
        tokens.append(f"<{node.tag}>")
        if node.text is not None:
            tokens += list(node.text)
        for n in node.getchildren():
            self.tokenize(n, tokens)
        if node.tag != "unk":
            tokens.append(f"</{node.tag}>")
        if node.tag != "td" and node.tail is not None:
            tokens += list(node.tail)

    def load_html_tree(self, node: Any, parent: TableTree | None = None) -> TableTree | None:
        """Converts HTML tree to the format required by apted"""
        if node.tag == "td":
            if self.structure_only:
                cell: list[str] = []
            else:
                tokens: list[str] = []
                self.tokenize(node, tokens)
                cell = tokens[1:-1].copy()
            new_node = TableTree(
                node.tag,
                int(node.attrib.get("colspan", "1")),
                int(node.attrib.get("rowspan", "1")),
                cell,
                *deque(),
            )
        else:
            new_node = TableTree(node.tag, None, None, None, *deque())
        if parent is not None:
            parent.children.append(new_node)
        if node.tag != "td":
            for n in node.getchildren():
                self.load_html_tree(n, new_node)
        if parent is None:
            return new_node
        return None

    def evaluate(self, pred: str, true: str) -> float:
        """Computes TEDS score between the prediction and the ground truth of a
        given sample
        """
        if (not pred) or (not true):
            return 0.0
        parser = html.HTMLParser(remove_comments=True, encoding="utf-8")
        pred_doc = html.fromstring(pred, parser=parser)
        true_doc = html.fromstring(true, parser=parser)
        if pred_doc.xpath("body/table") and true_doc.xpath("body/table"):
            pred_table = pred_doc.xpath("body/table")[0]
            true_table = true_doc.xpath("body/table")[0]
            if self.ignore_nodes:
                etree.strip_tags(pred_table, *self.ignore_nodes)
                etree.strip_tags(true_table, *self.ignore_nodes)
            n_nodes_pred = len(pred_table.xpath(".//*"))
            n_nodes_true = len(true_table.xpath(".//*"))
            n_nodes = max(n_nodes_pred, n_nodes_true)
            tree_pred = self.load_html_tree(pred_table)
            tree_true = self.load_html_tree(true_table)
            distance = APTED(tree_pred, tree_true, CustomConfig()).compute_edit_distance()
            return 1.0 - (float(distance) / n_nodes)
        return 0.0
