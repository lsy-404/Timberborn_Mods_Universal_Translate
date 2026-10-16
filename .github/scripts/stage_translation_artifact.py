#!/usr/bin/env python3
"""Copy only regular, top-level TOML files into a fresh upload directory.

This runs on the POSIX translation runner. Never pass untrusted data paths
directly to upload-artifact: it follows symlinks and traverses directories.
"""

import argparse
import os
from pathlib import Path
import shutil
import stat
import sys
import tempfile


def stage_translation_data(data_dir: Path, staging_parent: Path) -> Path:
    """Return an isolated snapshot, removing partial output on any failure."""
    data_dir = Path(data_dir).absolute()
    root_stat = data_dir.lstat()
    if not stat.S_ISDIR(root_stat.st_mode):
        raise ValueError('The data root must be a real directory, not a symlink')
    real_data = data_dir.resolve(strict=True)
    staging_parent = Path(staging_parent).resolve(strict=True)
    if staging_parent == real_data or real_data in staging_parent.parents:
        raise ValueError('The staging directory must be outside the data directory')

    # Anchor all reads to this directory and refuse a swapped-in symlink.
    root_fd = os.open(data_dir, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        if not os.path.samestat(root_stat, os.fstat(root_fd)):
            raise ValueError('The data directory changed during staging')
        staged = Path(tempfile.mkdtemp(prefix='translation-data-', dir=staging_parent))
        try:
            copied = 0
            for name in sorted(os.listdir(root_fd)):
                entry_stat = os.stat(name, dir_fd=root_fd, follow_symlinks=False)
                if stat.S_ISLNK(entry_stat.st_mode):
                    raise ValueError(f'Refusing symlink entry: {name!r}')
                if not name.endswith('.toml'):
                    continue
                if not stat.S_ISREG(entry_stat.st_mode):
                    raise ValueError(f'Refusing non-regular TOML entry: {name!r}')
                if (data_dir / name).resolve(strict=True).parent != real_data:
                    raise ValueError(f'TOML path escaped the data directory: {name!r}')

                # O_NOFOLLOW closes the check/open race; O_NONBLOCK also avoids
                # hanging if a regular file is replaced by a FIFO before open.
                flags = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK
                with os.fdopen(os.open(name, flags, dir_fd=root_fd), 'rb') as source:
                    opened_stat = os.fstat(source.fileno())
                    if (not stat.S_ISREG(opened_stat.st_mode)
                            or not os.path.samestat(entry_stat, opened_stat)):
                        raise ValueError(f'TOML entry changed during staging: {name!r}')
                    with (staged / name).open('xb') as destination:
                        shutil.copyfileobj(source, destination)
                copied += 1
            if not copied:
                raise ValueError('No top-level TOML files to stage')
        except BaseException:
            shutil.rmtree(staged)
            raise
        return staged
    finally:
        os.close(root_fd)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--data-dir', type=Path, default=Path('data'))
    parser.add_argument('--staging-parent', type=Path, required=True)
    args = parser.parse_args()

    staged = None
    try:
        staged = stage_translation_data(args.data_dir, args.staging_parent)
        # Publish the path only after the entire snapshot has succeeded.
        if os.environ.get('GITHUB_OUTPUT'):
            with open(os.environ['GITHUB_OUTPUT'], 'a', encoding='utf-8') as output:
                output.write(f'path={staged}\n')
    except (OSError, ValueError) as error:
        if staged is not None:
            shutil.rmtree(staged)
        print(f'Artifact staging failed: {error}', file=sys.stderr)
        return 1
    print(staged)
    return 0


if __name__ == '__main__':
    sys.exit(main())
