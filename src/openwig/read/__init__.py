"""openwig.read - read the open Bitwig project live (structure, notes, automation).

    from openwig.read import read_project
    data = read_project(bridge, with_clips=True)

Feed the result to `openwig.recreate.to_script(data)` to emit a Python script
that reconstructs the project.
"""
from openwig.read.project import read_project, summarize

__all__ = ["read_project", "summarize"]
