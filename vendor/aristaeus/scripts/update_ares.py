from subprocess import run

if __name__ == "__main__":
    run("git submodule deinit -f .")
    run("git submodule update --init")
    run("git submodule update --init --recursive --remote")
    run("poetry remove ares-sc2")

    with open("pyproject.toml") as f:
        contents = f.readlines()

    insert_at_index = 0
    for i, l in enumerate(contents):
        if l.strip() == "[tool.poetry.dependencies]":
            insert_at_index = i + 1
            break

    contents.insert(
        insert_at_index, 'ares-sc2 = { path = "ares-sc2", develop = false }\n'
    )

    with open("pyproject.toml", "w") as f:
        contents = "".join(contents)
        f.write(contents)

    run("poetry lock --no-update")
    run("poetry install")
