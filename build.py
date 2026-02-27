#   -*- coding: utf-8 -*-
from pybuilder.core import use_plugin, init, Author

use_plugin("python.core")
use_plugin("python.unittest")
use_plugin("python.flake8")
use_plugin("python.coverage")
use_plugin("python.distutils")


name = "MovingAverage_Advisor"
version = "0.1.0"

authors = [Author("Phodzo Muladi", "muladi.lione@gmail.com")]
default_task = "publish"

@init
def set_properties(project):
    project.set_property("flake8_break_build", True)
    project.set_property('coverage_break_build', False)  # Do not fail build on low test coverage
    project.set_property("dir_source_main_python", "src/main/python")
    project.set_property("dir_source_unittest_python", "src/tests/python")
    
    
    
    
    # DO NOT add 'unittest' here, it's built-in
    project.depends_on("pandas")
    project.depends_on("numpy")
    project.depends_on("matplotlib")
    project.depends_on("MetaTrader5")
    project.depends_on("pytest")
