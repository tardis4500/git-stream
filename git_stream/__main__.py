#!/usr/bin/env python3
"""
__main__ module for git-stream.

Enables use as module: $ python -m git-stream --version
"""

# TODO:
#   Store as a module and install with pip
#   Move config to OS correct location
#   Add to BatCave.cms
#       DATA_CACHE_DIR
#       branch property to return current branch name: _client.active_branch
#       delete remote branch: _client.git.push('origin', '--delete', branch)
#       new branch creation:
#           set remote: _client.git.checkout('-b', branch, '--track', branch_to_track)
#           no push
#       pull with rebase: _client.git.pull('--rebase', 'origin', branch)
#       reset: _client.git.reset(branch)
#       add all files: _client.git.add('--all')
#       commit without push: client.git.commit('-a', '-m', commit_message)
#       push to other branch: _client.git.push('origin', 'HEAD')
#       set remote: _client.git.branch('--set-upstream-to', branch)

# Import standard modules
from argparse import Namespace
from getpass import getuser
from pathlib import Path
from shutil import copyfile
from sys import exit as sys_exit, stderr, stdout

# Import third-party modules
from batcave.commander import Argument, Commander, SubParser
from batcave.cms import Client, ClientType
from batcave.lang import dotmap_to_yaml, yaml_to_dotmap
from batcave.sysutil import popd, pushd, rmpath, SysCmdRunner
from dotmap import DotMap
from git.exc import GitCommandError, InvalidGitRepositoryError

CONFIG = Path('~/.git-streams.yml').expanduser()
CONFIG_BAK = CONFIG.with_suffix('.bak')
CONFIG_SCHEMA = 1
STREAM_SCHEMA = 1
DEFAULTS = DotMap(default_parent='main',
                  default_remote='git@github.com:',
                  default_pr_reviewer='',
                  delivery_branch_template='%t_%d',
                  stream_branch_prefix=f'{getuser()}/',
                  stream_home=str(Path('~/git/streams').expanduser()))
DELIVERY_REPLACERS = {'%t': 'ticket',
                      '%d': 'description'}


class Stream:
    """Class to hold information about a stream."""
    def __init__(self):
        self.name = Path().cwd().name
        try:
            self._git_client = Client(ClientType.git, name=self.name, create=False)
        except InvalidGitRepositoryError:
            _exit('This is not a git repository.', 1)
        self._config = _read_config()
        if self.name not in self._config.streams:
            _exit(f'This stream is not defined: {self.name}', 1)
        self._definition = self._config.streams[self.name]
        if (branch := str(self._git_client._client.active_branch)) != self._definition.branch:  # pylint: disable=protected-access
            _exit(f'This stream is on the wrong branch ({branch}). Should be: {self._definition.branch}', 1)

    def __str__(self):
        return _get_stream_str(self.name, self._definition)

    _schema = property(lambda s: s._definition.schema if ('schema' in s._definition) else 0)

    def _store_stream(self) -> None:
        """Store the streams to the stream definition file."""
        _write_config(self._config)

    def add_parent(self, parent: str) -> None:
        """Add a parent to the current stream."""
        if parent in self._definition.parents:
            _exit(f'Parent "{parent}" already defined for stream "{self.name}"')
        self._definition.parents.append(parent)
        self._store_stream()

    def cleanup(self) -> None:
        """Cleanup the remote branch for the stream."""
        try:
            self._git_client._client.git.push('origin', '--delete', self._definition.branch)  # pylint: disable=protected-access
        except GitCommandError:
            pass

    def deliver(self, commit_message: str, create_pr: bool = False) -> None:
        """Create a pull request for delivery to the parent branch."""
        if not (delivery_branch := self._definition.delivery_branch):
            _exit('No delivery branch set for this stream.')
        branch = self._definition.branch
        origin_parent = 'origin/' + (target_parent := self._definition.parents[0])
        self._git_client.switch(branch)

        try:
            self._git_client._client.git.checkout('-b', delivery_branch, '--track', origin_parent)  # pylint: disable=protected-access
            first_delivery = True
        except GitCommandError as err:
            if 'exit code(128)' not in str(err):
                raise
            self._git_client.switch(delivery_branch)
            first_delivery = False

        if first_delivery:
            self._git_client._client.git.pull('--rebase', 'origin', branch)  # pylint: disable=protected-access
            self._git_client._client.git.reset(origin_parent)  # pylint: disable=protected-access
            self._git_client._client.git.add('--all')  # pylint: disable=protected-access
            try:
                self._git_client._client.git.commit('-a', '-m', commit_message)  # pylint: disable=protected-access
            except GitCommandError as err:
                if 'Your branch is up to date' not in str(err):
                    raise
                _exit('There are no changes to deliver', 1)
            self._git_client._client.git.push('origin', 'HEAD')  # pylint: disable=protected-access
            self._git_client._client.git.branch('--set-upstream-to', f'origin/{delivery_branch}')  # pylint: disable=protected-access
        else:
            self._git_client.merge(branch, checkin_message=commit_message)

        if create_pr:
            if 'github' not in self._definition.repo:
                _exit('Unable to create PR for non-GitHub repo.')
            SysCmdRunner('gh', 'pr', 'create', fill=True, base=target_parent,
                         reviewer=self._definition.pr_reviewer if self._definition.pr_reviewer else self._config.default_pr_reviewer).run()
        self._git_client.switch(branch)

    def rm_parent(self, parent: str) -> None:
        """Remove a parent to the current stream."""
        if parent not in self._definition.parents:
            _exit(f'Parent "{parent}" not defined for stream "{self.name}"')
        self._definition.parents = list(set(self._definition.parents) - set((parent,)))
        self._store_stream()

    def set_value(self, parameter: str, value: str) -> None:
        """Set the specified parameter value for the current stream."""
        if value:
            self._definition[parameter] = value
        elif parameter in self._definition:
            del self._definition[parameter]
        self._store_stream()

    def show(self) -> None:
        """Print the string representation."""
        print(str(self))

    def update(self) -> None:
        """Update a stream from the parents."""
        if self._git_client:
            for parent in self._definition.parents:
                print(f'Updating from origin/{parent}')
                self._git_client.switch(parent)
                self._git_client.update()
                self._git_client.switch(self._definition.branch)
                self._git_client.merge(parent, checkin_message=f'Update from {parent}.')
        else:
            _exit('This repository is not initialized.', 1)


def main() -> None:
    """The main entry point."""
    if not CONFIG.exists():
        _write_config(DEFAULTS | DotMap(schema=CONFIG_SCHEMA, streams={}))
    Commander('Git Stream Manager', subparsers=[SubParser('add_parent', lambda a: stream_action('add_parent', **vars(a)), [Argument('parent')]),
                                                SubParser('config', configurator, [Argument('-s', '--set')]),
                                                SubParser('create', create, [Argument('-p', '--parent'), Argument('-t', '--ticket'), Argument('-d', '--delivery-branch'),
                                                                             Argument('name'), Argument('repo')]),
                                                SubParser('deliver', lambda a: stream_action('deliver', **vars(a)), [Argument('-p', '--create-pr', action='store_true'),
                                                                                                                     Argument('commit_message')]),
                                                SubParser('list', list_streams),
                                                SubParser('rm', rm_stream, [Argument('-c', '--cleanup', action='store_true'), Argument('name')]),
                                                SubParser('rm_parent', lambda a: stream_action('rm_parent', **vars(a)), [Argument('parent')]),
                                                SubParser('set_value', lambda a: stream_action('set_value', **vars(a)), [Argument('parameter'), Argument('value')]),
                                                SubParser('show', lambda a: stream_action('show')),
                                                SubParser('update', lambda a: stream_action('update'))]).execute()


def _exit(message: str, exit_code: int = 0) -> None:
    print(message, file=(stderr if exit_code else stdout))
    sys_exit(exit_code)


def _get_stream_str(stream_name: str, stream_info: DotMap) -> str:
    string_rep = f'name: {stream_name}\n'
    for (key, val) in stream_info.items():
        string_rep += f'    {key}: {val}\n' if (key != 'schema') else ''
    return string_rep


def _read_config() -> DotMap:
    if (schema := (config := yaml_to_dotmap(CONFIG)).get('schema', 0)) != CONFIG_SCHEMA:
        print(f'Configuration at wrong schema: {schema}; expected: {CONFIG_SCHEMA}', file=stderr)
        sys_exit(1)
    return config


def _write_config(new_config: DotMap) -> None:
    if CONFIG.exists():
        copyfile(CONFIG, CONFIG_BAK)
    try:
        dotmap_to_yaml(new_config, CONFIG)
    except:  # noqa:E722
        if CONFIG_BAK.exists():
            copyfile(CONFIG_BAK, CONFIG)
        raise


def configurator(args: Namespace) -> None:
    """List all the streams."""
    config = _read_config()
    if not args.set:
        for (item, value) in config.items():
            if item not in ('schema', 'streams'):
                print(f'  {item}: {value}')
        return
    (item, value) = args.set.split('=', 1)
    if item in ('schema', 'streams'):
        print(f'The value of {item} is readonly.', file=stderr)
        sys_exit(1)
    if item not in config:
        print(f'Not a valid configuration value: {item}.', file=stderr)
        sys_exit(1)
    config[item] = value
    _write_config(config)


def create(args: Namespace) -> None:
    """Create a stream."""
    config = _read_config()
    repo = args.repo if args.repo.startswith('git@') else f'{config.default_remote}{args.repo}.git'
    repo_name = repo.split('/')[-1].split('.')[0]
    stream_branch = f'{config.stream_branch_prefix}{args.name}'
    stream_name = f'{repo_name}-' + stream_branch.replace('/', '-')
    parent = args.parent if args.parent else config.default_parent
    if stream_name in config.streams:
        _exit(f'Stream already defined: {stream_name}', 1)
    with Client(ClientType.git, stream_name,
                connect_info=repo,
                root=Path(config.stream_home) / stream_name,
                branch=parent,
                create=True, cleanup=False) as git_client:
        try:
            git_client.switch(stream_branch)
        except GitCommandError as err:
            if 'did not match any file(s) known to git' not in str(err):
                raise
            git_client.create_branch(stream_branch)
    config.streams[stream_name] |= DotMap(repo=repo,
                                          description=args.name,
                                          branch=stream_branch,
                                          parents=[parent],
                                          schema=STREAM_SCHEMA)
    for item in ('delivery_branch', 'ticket'):
        if value := getattr(args, item):
            config.streams[stream_name][item] = value
    if config.delivery_branch_template and ('delivery_branch' not in config.streams[stream_name]):
        delivery_branch = config.delivery_branch_template
        for (var, val) in DELIVERY_REPLACERS.items():
            if var in config.delivery_branch_template:
                if val not in config.streams[stream_name]:
                    print(f'Unable to set delivery branch since {val} is not set.', file=stderr)
                    delivery_branch = ''
                    break
                delivery_branch = delivery_branch.replace(var, config.streams[stream_name][val])
        if delivery_branch:
            config.streams[stream_name].delivery_branch = delivery_branch
    _write_config(config)


def list_streams(*unused_args) -> None:
    """List all the streams."""
    for (stream, info) in _read_config().streams.items():
        print(_get_stream_str(stream, info))


def rm_stream(args: Namespace) -> None:
    """Create a stream."""
    config = _read_config()
    if args.name not in config.streams:
        _exit(f'Stream not defined: {args.name}', 1)
    if args.cleanup:
        pushd(stream_root := Path(config.stream_home, args.name))
        Stream().cleanup()
        popd()
        rmpath(stream_root)
    del config.streams[args.name]
    _write_config(config)


def stream_action(action: str, **kwargs) -> None:
    """List the streams for the current repository."""
    if 'command' in kwargs:
        del kwargs['command']
    getattr(Stream(), action)(**kwargs)


if __name__ == '__main__':
    main()

# cSpell:ignore batcave dotmap cysiv checkin
