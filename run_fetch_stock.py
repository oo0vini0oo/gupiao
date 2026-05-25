#!/usr/bin/env python3
"""Launcher for stock fetcher - Task Scheduler wrapper"""
import os
import sys

os.chdir(os.path.dirname(__file__))
sys.path.insert(0, os.path.dirname(__file__))

from main import main
main()