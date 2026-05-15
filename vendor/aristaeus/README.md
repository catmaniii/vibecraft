# ares-sc2-starter-bot

[ares-sc2 repo](https://github.com/AresSC2/ares-sc2) <br>
[ares-sc2 docs](https://aressc2.github.io/ares-sc2/index.html)

## Getting started
Please refer to [this tutorial article](https://aressc2.github.io/ares-sc2/tutorials/installation.html)
which is based on this starter bot repo.

## Uploading to [AiArena](https://www.sc2ai.com)

<b>TLDR:</b> On each push to `main` there is a Github Actions workflow that builds a ladder ready zip. Take a 
look on the `Actions` tab to download.

Included in this repository is a convenient script named `scripts/create_ladder_zip.py`. 
However, it is important to note that the AIarena ladder infrastructure operates specifically 
on Linux-based systems. Due to the dependency of ares-sc2 on cython, it is necessary to execute 
this script on a Linux environment in order to generate Linux binaries.

To streamline this process, a GitHub workflow has been integrated into this repository when pushing to `main` 
on your GitHub repository (if you previously created a template from the 
[starter-bot](https://github.com/AresSC2/ares-sc2-starter-bot)). 
Upon each push to the main branch, the `create_ladder_zip.py` script is automatically executed on a 
Debian-based system. As a result, a compressed artifact named `ladder-zip.zip` is generated, 
facilitating the subsequent upload to AIarena. To access the generated file, navigate to the Actions tab, 
click on an Action and refer to the Artifacts section. Please note this may take a few
minutes after pusing to the `main` branch.
