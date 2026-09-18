from aimemory.projects import identify_project, normalize_git_remote


def test_normalize_git_remote_equates_ssh_and_https():
    assert normalize_git_remote("git@github.com:User/Project.git") == "github.com/user/project"
    assert normalize_git_remote("https://github.com/User/Project.git") == "github.com/user/project"


def test_identify_project_prefers_git_remote():
    project = identify_project("/Users/a/elsewhere", "git@github.com:User/Project.git", "main")

    assert project is not None
    assert project.name == "project"
    assert project.git_remote == "github.com/user/project"
    assert project.git_branch == "main"
