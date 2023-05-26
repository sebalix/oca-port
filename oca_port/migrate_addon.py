# Copyright 2022 Camptocamp SA
# License LGPL-3.0 or later (http://www.gnu.org/licenses/lgpl)

import os
import tempfile
import urllib.parse

import click

from .port_addon_pr import PortAddonPullRequest
from .utils import git as g
from .utils.misc import Output, bcolors as bc

MIG_BRANCH_NAME = "{branch}-mig-{addon}"
MIG_MERGE_COMMITS_URL = (
    "https://github.com/OCA/maintainer-tools/wiki/Merge-commits-in-pull-requests"
)
MIG_TASKS_URL = (
    "https://github.com/OCA/maintainer-tools/wiki/Migration-to-version-{branch}"
    "#tasks-to-do-in-the-migration"
)
MIG_NEW_PR_TITLE = "[{to_branch}][MIG] {addon}"
MIG_NEW_PR_URL = (
    "https://github.com/{upstream_org}/{repo_name}/compare/"
    "{to_branch}...{user_org}:{mig_branch}?expand=1&title={title}"
)
MIG_TIPS = "\n".join(
    [
        f"\n{bc.BOLD}{bc.OKCYAN}The next steps are:{bc.END}",
        ("\t1) Reduce the number of commits " f"('{bc.DIM}OCA Transbot...{bc.END}'):"),
        f"\t\t=> {bc.BOLD}{MIG_MERGE_COMMITS_URL}{bc.END}",
        "\t2) Adapt the module to the {to_branch} version:",
        f"\t\t=> {bc.BOLD}" "{mig_tasks_url}" f"{bc.END}",
        (
            "\t3) On a shell command, type this for uploading the content to GitHub:\n"
            f"{bc.DIM}"
            "\t\t$ git add --all\n"
            '\t\t$ git commit -m "[MIG] {addon}: Migration to {to_branch}"\n'
            "\t\t$ git push {fork} {mig_branch} --set-upstream"
            f"{bc.END}"
        ),
        "\t4) Create the PR against {upstream_org}/{repo_name}:",
        f"\t\t=> {bc.BOLD}" "{new_pr_url}" f"{bc.END}",
    ]
)
BLACKLIST_TIPS = "\n".join(
    [
        f"\n{bc.BOLD}{bc.OKCYAN}The next steps are:{bc.END}",
        (
            "\t1) On a shell command, type this for uploading the content to GitHub:\n"
            f"{bc.DIM}"
            "\t\t$ git push {fork} {mig_branch} --set-upstream"
            f"{bc.END}"
        ),
        "\t2) Create the PR against {upstream_org}/{repo_name}:",
        f"\t\t=> {bc.BOLD}" "{new_pr_url}" f"{bc.END}",
    ]
)


class MigrateAddon(Output):
    def __init__(self, app):
        self.app = app
        self.settings = self.app.settings
        self._results = {"process": "migrate", "results": None}
        self.mig_branch = g.Branch(
            self.settings.repo,
            MIG_BRANCH_NAME.format(
                branch=self.settings.to_branch.name[:4], addon=self.settings.addon
            ),
        )

    def run(self):
        blacklisted = self.app.storage.is_addon_blacklisted()
        if blacklisted:
            self._print(
                f"{bc.DIM}Migration of {bc.BOLD}{self.settings.addon}{bc.END} "
                f"{bc.DIM}to {self.settings.to_branch.name} "
                f"blacklisted ({blacklisted}){bc.ENDD}"
            )
            return False
        if self.settings.non_interactive:
            # If an output is defined we return the result in the expected format
            if self.settings.output:
                self._results["results"] = True
                return self._render_output(self.settings.output, self._results)
            if self.settings.cli:
                # Exit with an error code if the addon is eligible for a migration
                # User-defined exit codes should be defined between 64 and 113.
                # Allocate 105 for 'PortAddonPullRequest'.
                raise SystemExit(100)
            return True
        confirm = (
            f"Migrate {bc.BOLD}{self.settings.addon}{bc.END} "
            f"from {bc.BOLD}{self.settings.from_branch.name}{bc.END} "
            f"to {bc.BOLD}{self.settings.to_branch.name}{bc.END}?"
        )
        if not click.confirm(confirm):
            self.app.storage.blacklist_addon(confirm=True)
            if not self.app.storage.dirty:
                return False
        # Check if a migration PR already exists
        # TODO
        if not self.settings.fork:
            raise click.UsageError("Please set the '--fork' option")
        if self.settings.repo.untracked_files:
            raise click.ClickException("Untracked files detected, abort")
        self._checkout_base_branch()
        if self._create_mig_branch():
            # Case where the addon shouldn't be ported (blacklisted)
            if self.app.storage.dirty:
                self.app.storage.commit()
                self._print_tips(blacklisted=True)
                return False
            with tempfile.TemporaryDirectory() as patches_dir:
                self._generate_patches(patches_dir)
                self._apply_patches(patches_dir)
            g.run_pre_commit(self.settings.repo, self.settings.addon)
        # Check if the addon has commits that update neighboring addons to
        # make it work properly
        PortAddonPullRequest(
            self.settings, create_branch=False, push_branch=False
        ).run()
        self._print_tips()
        return True

    def _checkout_base_branch(self):
        # Ensure to not start to work from a working branch
        if self.settings.to_branch.name in self.settings.repo.heads:
            self.settings.repo.heads[self.settings.to_branch.name].checkout()
        else:
            self.settings.repo.git.checkout(
                "--no-track",
                "-b",
                self.settings.to_branch.name,
                self.settings.to_branch.ref(),
            )

    def _create_mig_branch(self):
        create_branch = True
        if self.mig_branch.name in self.settings.repo.heads:
            confirm = (
                f"Branch {bc.BOLD}{self.mig_branch.name}{bc.END} already exists, "
                "recreate it?\n(⚠️  you will lose the existing branch)"
            )
            if click.confirm(confirm):
                self.settings.repo.delete_head(self.mig_branch.name, "-f")
            else:
                create_branch = False
        if create_branch:
            # Create branch
            print(
                f"\tCreate branch {bc.BOLD}{self.mig_branch.name}{bc.END} "
                f"from {self.settings.to_branch.ref()}..."
            )
            self.settings.repo.git.checkout(
                "--no-track", "-b", self.mig_branch.name, self.settings.to_branch.ref()
            )
        return create_branch

    def _generate_patches(self, patches_dir):
        print("\tGenerate patches...")
        self.settings.repo.git.format_patch(
            "--keep-subject",
            "-o",
            patches_dir,
            f"{self.settings.to_branch.ref()}..{self.settings.from_branch.ref()}",
            "--",
            self.settings.addon,
        )

    def _apply_patches(self, patches_dir):
        patches = [
            os.path.join(patches_dir, f) for f in sorted(os.listdir(patches_dir))
        ]
        # Apply patches with git-am
        print(f"\tApply {len(patches)} patches...")
        self.settings.repo.git.am("-3", "--keep", *patches)
        print(
            f"\t\tCommits history of {bc.BOLD}{self.settings.addon}{bc.END} "
            f"has been migrated."
        )

    def _print_tips(self, blacklisted=False):
        mig_tasks_url = MIG_TASKS_URL.format(branch=self.settings.to_branch.name)
        pr_title_encoded = urllib.parse.quote(
            MIG_NEW_PR_TITLE.format(
                to_branch=self.settings.to_branch.name[:4], addon=self.settings.addon
            )
        )
        new_pr_url = MIG_NEW_PR_URL.format(
            upstream_org=self.settings.upstream_org,
            repo_name=self.settings.repo_name,
            to_branch=self.settings.to_branch.name,
            user_org=self.settings.user_org,
            mig_branch=self.mig_branch.name,
            title=pr_title_encoded,
        )
        if blacklisted:
            tips = BLACKLIST_TIPS.format(
                upstream_org=self.settings.upstream_org,
                repo_name=self.settings.repo_name,
                fork=self.settings.fork,
                mig_branch=self.mig_branch.name,
                new_pr_url=new_pr_url,
            )
            print(tips)
            return
        tips = MIG_TIPS.format(
            upstream_org=self.settings.upstream_org,
            repo_name=self.settings.repo_name,
            addon=self.settings.addon,
            to_branch=self.settings.to_branch.name,
            fork=self.settings.fork,
            mig_branch=self.mig_branch.name,
            mig_tasks_url=mig_tasks_url,
            new_pr_url=new_pr_url,
        )
        print(tips)
