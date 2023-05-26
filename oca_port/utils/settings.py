# Copyright 2023 Camptocamp SA
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl)

import pathlib
from dataclasses import dataclass

import git

from .git import Branch


@dataclass
class Settings:
    """Define the settings used by oca-port to perform its actions.

    Parameters:

        from_branch:
            the source branch (e.g. '15.0')
        to_branch:
            the source branch (e.g. '16.0')
        addon:
            the name of the module to process
        repo_path:
            local path to the Git repository
        fork:
            name of the Git remote used as fork
        repo_name:
            name of the repository on the upstream organization (e.g. 'server-tools')
        user_org:
            name of the user's GitHub organization where the fork is hosted
        upstream_org:
            name of the upstream GitHub organization (default = 'OCA')
        upstream:
            name of the Git remote considered as the upstream (default = 'origin')
        verbose:
            returns more details to the user
        non_interactive:
            flag to not wait for user input and to return a error code to the shell.
            Returns 100 if an addon could be migrated, 110 if pull requests/commits
            could be ported, 0 if the history of the addon is the same on both branches.
        no_cache:
            flag to disable the user's cache
        clear_cache:
            flag to remove the user's cache once the process is done
    """

    from_branch: str
    to_branch: str
    addon: str
    repo_path: str
    fork: str = None
    repo_name: str = None
    user_org: str = None
    upstream_org: str = "OCA"
    upstream: str = "origin"
    verbose: bool = False
    non_interactive: bool = False
    no_cache: bool = False
    clear_cache: bool = False
    cli: bool = False  # Not documented, should not be used outside of the CLI

    def __post_init__(self):
        # Handle with repo_path and repo_name
        if self.repo_path:
            self.repo_path = pathlib.Path(self.repo_path)
        else:
            raise ValueError("'repo_path' has to be set.")
        if not self.repo_name:
            self.repo_name = self.repo_path.name
        # Handle Git repository
        self.repo = git.Repo(self.repo_path)
        if self.repo.is_dirty(untracked_files=True):
            raise ValueError("changes not committed detected in this repository.")
        # Handle user's organization and fork
        if not self.user_org:
            # Assume that the fork remote has the same name than the user organization
            self.user_org = self.fork
        if self.fork:
            if self.fork not in self.repo.remotes:
                raise ForkValueError(self.repo_name, self.fork)
        # Transform branch strings to Branch objects
        try:
            self.from_branch = Branch(
                self.repo, self.from_branch, default_remote=self.upstream
            )
            self.to_branch = Branch(
                self.repo, self.to_branch, default_remote=self.upstream
            )
        except ValueError as exc:
            if exc.args[1] not in self.repo.remotes:
                raise RemoteBranchValueError(self.repo_name, exc.args[1]) from exc
        # Force non-interactive mode is we are not in CLI mode
        if not self.cli:
            self.non_interactive = True


class ForkValueError(ValueError):
    pass


class RemoteBranchValueError(ValueError):
    pass
