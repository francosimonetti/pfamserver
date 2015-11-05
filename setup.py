#!/usr/bin/env python
# -*- coding: utf-8 -*-
from __future__ import print_function
from distutils.core import setup
from distutils.command.build import build
from setuptools.command import easy_install
import os
import subprocess
from urllib import urlretrieve
from datetime import datetime
import sys


def parse_requirements(filename):
    return list(filter(lambda line: (line.strip())[0] != '#',
                       [line.strip() for line in open(filename).readlines()]))


def calculate_version():
    # Fetch version from git tags, and write to version.py.
    # Also, when git is not available (PyPi package), use stored version.py.
    version_py = os.path.join(os.path.dirname(__file__), 'version.py')
    try:
        version_git = subprocess.check_output(["git", "describe"]).rstrip()
    except Exception:
        with open(version_py, 'r') as fh:
            version_git = (open(version_py).read()
                           .strip().split('=')[-1].replace('"', ''))
    version_msg = ('# Do not edit this file, pipeline versioning is '
                   'governed by git tags')
    with open(version_py, 'w') as fh:
        fh.write(version_msg + os.linesep + "__version__=" + version_git)
    return version_git


REQUIREMENTS = parse_requirements('requirements.txt')
VERSION_GIT = calculate_version()
import platform as p
from distutils.sysconfig import get_python_lib


TMP_PATH = get_python_lib() + '/'
OS_NAME = p.system()
BINARIES = {
    'hmmer': {
        'version': '3.1b2',
        'name': 'hmmer-{:s}',
        'url': 'http://selab.janelia.org/software/hmmer3/{:s}',
        'compile': {
            'depends': [],
            'config': {
                'pre': ('LDFLAGS=-L/usr/local/lib '
                        'CPPFLAGS=-I/usr/local/include '
                        'LD_LIBRARY_PATH=/usr/local'),
                'post': (' --prefix=/usr/local'),
            }
        }
    }
}

SYSTEMS = {
    'Linux': {
        'update_shared_libs': '',
        'libs': {
            'hmmer': TMP_PATH + 'hmmer/easel/miniapps/esl-afetch',
        },
    },
    'Darwin': {
        'update_shared_libs': '',
        'libs': {
            'hmmer': TMP_PATH + 'hmmer/easel/miniapps/esl-afetch',
        },
    },
}


def get_long_description():
    readme_file = 'README.md'
    if not os.path.isfile(readme_file):
        return ''
    # Try to transform the README from Markdown to reStructuredText.
    try:
        easy_install.main(['-U', 'pyandoc==0.0.1'])
        import pandoc
        pandoc.core.PANDOC_PATH = 'pandoc'
        doc = pandoc.Document()
        doc.markdown = open(readme_file).read()
        description = doc.rst
    except Exception:
        description = open(readme_file).read()
    return description


class Builder(object):

    def __init__(self, lib):
        self.lib_key = lib
        self.lib = BINARIES[lib]
        self.name = self.lib['name'].format(self.lib['version'])
        self.local_extracted = '{:s}{:s}'.format(TMP_PATH, self.name)
        self.local_unpacked = '{:s}{:s}'.format(TMP_PATH, lib)
        self.local_filename = ''

    def call(self, cmd):
        return subprocess.call(cmd, shell=True)

    def download(self):
        url = self.lib['url']
        if url.find('{:s}') > 0:
            url = url.format(self.lib['version'])
        filename = '{:s}.tar.gz'.format(self.name)
        self.local_filename = '{:s}{:s}'.format(TMP_PATH, filename)
        if not os.path.isfile(self.local_filename):
            begin = datetime.now()

            def dl_progress(count, block_size, total_size):
                transfered = (count * block_size
                              if total_size >= count * block_size
                              else total_size)
                progress = transfered * 100. / total_size
                speed = (transfered /
                         ((datetime.now() - begin).total_seconds())) / 1024
                print('\r{:s}'.format(' ' * 78), end=' ')
                print(u'\rDownloaded {:s} (\033[33m{:03.2f} %\033[0m '
                      'at \033[35m{:10.0f} KB/s\033[0m)'.format(
                          filename, progress, speed), end=' ')
                sys.stdout.flush()
            source = '{:s}/{:s}'.format(url, filename)
            destiny = '{:s}{:s}'.format(TMP_PATH, filename)
            self.local_filename, _ = urlretrieve(source, destiny,
                                                 reporthook=dl_progress)

    def uncompress(self):
        if not os.path.isdir(self.local_unpacked):
            print('->', self.local_unpacked)
            self.download()
            # self.call('rm -rf {:s}*'.format(self.local_unpacked))
            import tarfile as tar
            tfile = tar.open(self.local_filename, mode='r:gz')
            tfile.extractall(TMP_PATH)
            tfile.close()
            self.call('mv {:s} {:s}'.format(self.local_extracted,
                                            self.local_unpacked))
            self.call('chmod -R ugo+rwx {:s}'.format(self.local_unpacked))

    def build(self):
        depends = self.lib['compile']['depends']
        install = lambda dep: easy_install.main(['-U', dep])
        map(install, depends)
        filename = SYSTEMS[OS_NAME]['libs'][self.lib_key]
        if not os.path.isfile(filename):
            self.uncompress()
            title = '{:s} {:s}'.format(OS_NAME, p.architecture()[0])
            spacer = '-' * len(title)
            print('+{:s}+\n|{:s}|\n+{:s}+'.format(spacer, title, spacer))
            import multiprocessing
            self.call('rm {:s}'.format(filename))
            path = self.local_unpacked
            config = self.lib['compile']['config']
            ncores = multiprocessing.cpu_count()
            self.call(('cd {:s}; {:s} ./configure {:s}; make -j {:d}').format(
                           path, config['pre'], config['post'], ncores))
            update_shared_libs = SYSTEMS[OS_NAME]['update_shared_libs']
            if update_shared_libs:
                self.call(update_shared_libs)


class build_wrapper(build):
    def initialize_options(self):
        # Deploy all the described libraries in the BINARIES dictionary.
        libs = sorted(BINARIES.keys())
        build_lib = lambda lib: Builder(lib).build()
        map(build_lib, libs)
        return build.initialize_options(self)


setup(
    name='pfamserver',
    version=VERSION_GIT,
    author=u'Eloy Adonis Colell',
    author_email='eloy.colell@gmail.com',
    packages=['pfamserver'],
    url='https://github.com/ecolell/pfamserver',
    license='MIT',
    description=('A python service to query the PFAM database through a '
                 'JSON api.'),
    long_description=get_long_description(),
    zip_safe=True,
    install_requires=REQUIREMENTS,
    classifiers=[
        "Intended Audience :: Science/Research",
        "Programming Language :: Python",
        "Programming Language :: Python :: 2",
        "Programming Language :: Python :: 2.6",
        "Programming Language :: Python :: 2.7",
        "Programming Language :: Python :: 3",
        "Programming Language :: Python :: 3.2",
        "Programming Language :: Python :: 3.3",
    ],
    cmdclass={
        'build': build_wrapper,
    },
)
