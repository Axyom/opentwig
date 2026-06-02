"""Bundled Bitwig controller script (`openwig_bridge.control.js`).

This package directory is shipped as `package_data` so `openwig install`
can locate and copy the controller into Bitwig's user-scripts folder.

    from importlib.resources import files
    src = files("openwig.controller").joinpath("openwig_bridge.control.js")
"""
