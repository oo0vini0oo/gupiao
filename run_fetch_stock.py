#!/usr/bin/env python3
"""Launcher for stock fetcher - ASCII path wrapper for Task Scheduler"""
import os
import sys

# Change to the stock analysis directory (Unicode path)
stock_dir = os.path.join(os.path.dirname(__file__), "股票分析")
os.chdir(stock_dir)
sys.path.insert(0, stock_dir)

from main import main
main()
