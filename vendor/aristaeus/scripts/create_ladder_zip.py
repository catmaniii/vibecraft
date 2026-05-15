"""
Zips the relevant files and directories so that Eris
can be uploaded to ladder and tournaments.
TODO: check all files and folders are present before zipping
"""
import platform
from os import path, remove, walk
from subprocess import Popen, run
from typing import Dict, List, Tuple
import shutil
import os
import zipfile
import site

import yaml

MY_BOT_NAME: str = "MyBotName"
ZIPFILE_NAME: str = "bot.zip"

CONFIG_FILE: str = "config.yml"
ZIP_FILES: List[str] = [
    "config.yml",
    "config.yaml",
    "ladder.py",
    "run.py",
    "terran_builds.yml",
    "terran_builds.yaml",
    "protoss_builds.yml",
    "protoss_builds.yaml",
    "zerg_builds.yml",
    "zerg_builds.yaml",
]
if platform.system() == "Windows":
    FILETYPES_TO_IGNORE: Tuple = (".c", ".so", "pyx")
    ROOT_DIRECTORY = "./"
else:
    FILETYPES_TO_IGNORE: Tuple = (".c", ".pyd", "pyx", "pyi")
    ROOT_DIRECTORY = "./"

ZIP_DIRECTORIES: Dict[str, Dict] = {
    "bot": {"zip_all": True, "folder_to_zip": "bot"},
    "ares-sc2": {"zip_all": True, "folder_to_zip": ""},
    "python-sc2": {"zip_all": False, "folder_to_zip": "sc2"},
}


def zip_dir(dir_path, zip_file):
    """
    Will walk through a directory recursively and add all folders and files to zipfile
    @param dir_path:
    @param zip_file:
    @return:
    """
    for root, _, files in walk(dir_path):
        if "ares-sc2/build" in root or "ares-sc2/dist" in root:
            continue
        for file in files:
            if file.lower().endswith(FILETYPES_TO_IGNORE):
                continue
            zip_file.write(
                path.join(root, file),
                path.relpath(path.join(root, file), path.join(dir_path, "..")),
            )


def zip_files_and_directories(zipfile_name: str) -> None:
    """
    @return:
    """

    path_to_zipfile = path.join(ROOT_DIRECTORY, zipfile_name)
    # if the zip file already exists remove it
    if path.isfile(path_to_zipfile):
        remove(path_to_zipfile)
    # create a new zip file
    zip_file = zipfile.ZipFile(path_to_zipfile, "w", zipfile.ZIP_DEFLATED)

    # write directories to the zipfile
    for directory, values in ZIP_DIRECTORIES.items():
        if values["zip_all"]:
            zip_dir(path.join(ROOT_DIRECTORY, directory), zip_file)
        else:
            path_to_dir = path.join(ROOT_DIRECTORY, directory, values["folder_to_zip"])
            zip_dir(path_to_dir, zip_file)

    # write individual files
    for single_file in ZIP_FILES:
        _path: str = path.join(ROOT_DIRECTORY, single_file)
        if path.isfile(_path):
            zip_file.write(_path, single_file)

    # close the zip file
    zip_file.close()


def get_sc2_library(library_name, project_directory):
    # Find the site packages directory

    site_packages_dir = site.getsitepackages()[0]

    # Construct the library path
    library_path = os.path.join(site_packages_dir, "Lib", "site-packages", library_name)

    # Check if the library path exists
    if not os.path.exists(library_path):
        raise ValueError(f"Library '{library_name}' not found in site packages.")

    # Determine the destination directory in the zip file
    destination_directory = os.path.join(project_directory, library_name)

    # Remove the destination directory if it already exists
    if os.path.exists(destination_directory):
        shutil.rmtree(destination_directory)

    # Copy the library directory into the project directory
    shutil.copytree(library_path, destination_directory)


def check_git_status():
    """
    Make sure the branch is master and has no uncommitted changes.
    Not currently used
    @return:
    """
    difference = run("git diff", capture_output=True, text=True)
    branch_name = run("git rev-parse --abbrev-ref HEAD", capture_output=True, text=True)
    assert not difference.stdout, "Uncommitted changes are present"
    assert branch_name.stdout.strip() == "master", "This is not the master branch"


def check_config_values():
    """
    Make sure debug is False.
    """
    config_path: str = path.join(ROOT_DIRECTORY, CONFIG_FILE)
    if path.isfile(config_path):
        with open(path.join(ROOT_DIRECTORY, CONFIG_FILE), "r") as f:
            config = yaml.safe_load(f)
        assert not config["Debug"], "Debug is not False"


def get_zipfile_name() -> str:
    """Attempt to get bot name from config."""
    __user_config_location__: str = path.abspath(".")
    user_config_path: str = path.join(__user_config_location__, CONFIG_FILE)
    zipfile_name = ZIPFILE_NAME
    # attempt to get race and bot name from config file if they exist
    if path.isfile(user_config_path):
        with open(user_config_path) as config_file:
            config: dict = yaml.safe_load(config_file)
            if MY_BOT_NAME in config:
                zipfile_name = f"{config[MY_BOT_NAME]}.zip"
    return zipfile_name


def on_error(func, path, exc_info):
    """
    Error handler for ``shutil.rmtree``.

    If the error is due to an access error (read only file)
    it attempts to add write permission and then retries.

    If the error is for another reason it re-raises the error.

    Usage : ``shutil.rmtree(path, onerror=onerror)``
    """
    import stat

    # Is the error an access error?
    if not os.access(path, os.W_OK):
        os.chmod(path, stat.S_IWUSR)
        func(path)
    else:
        raise


if __name__ == "__main__":
    print("Cloning python-sc2...")
    destination_directory = os.path.join("../", "python-sc2")
    if os.path.exists(destination_directory):
        shutil.rmtree(destination_directory, ignore_errors=False, onerror=on_error)

    run("git clone https://github.com/august-k/python-sc2", shell=True)

    # get name of bot from config if possible (otherwise use default name)
    # zipfile_name = get_zipfile_name()
    zipfile_name = ZIPFILE_NAME
    print("Setting up poetry environment...")
    # ensure env is setup and dependencies are installed
    p = Popen(["poetry", "install"], cwd=f"{ROOT_DIRECTORY}")
    # makes the process wait, otherwise files get zipped before compile is complete
    p.communicate()
    p_status = p.wait()

    # compile the cython code
    print("Compiling cython code...")
    p = Popen(["poetry", "build"], cwd=f"{ROOT_DIRECTORY}ares-sc2")
    # makes the process wait, otherwise files get zipped before compile is complete
    p.communicate()
    p_status = p.wait()

    # at the moment -> ensure debug=False
    print("Checking config values...")
    check_config_values()

    print("Copying sc2 folder from site packages...")

    print(f"Zipping files and directories to {zipfile_name}...")
    # copy everything we need into a zip file
    zip_files_and_directories(zipfile_name)

    print(f"Cleaning up...")

    destination_directory = os.path.join("./", "python-sc2")
    if os.path.exists(destination_directory):
        shutil.rmtree(destination_directory, onerror=on_error)

    print(f"Ladder zip complete.")
