=======
MetaPkg
=======

MetaPkg is a framework that allows building native packages and installers
for a variety of OSes and distributions in a way that ensures maximum
compatibility and integration with the target platform.

How it works
------------

MetaPkg works by taking a package specification, generating a build script
that is most appropriate for the target platform, and then running the build
script directly on the target platform to produce the package artifacts.
For example, on RHEL targets MetaPkg generates an RPM .spec file and the
associated files and runs ``rpmbuild`` to produce a well-behaved RPM package.
On Debian targets ``dpkg-buildpackage`` is used and so on.

MetaPkg contains a builtin dependency resolver, based on
`Poetry <https://github.com/python-poetry/poetry>`_ that uses the native
platform's package manager to find the necessary dependencies.  This makes
it possible to use the libraries provided by the system and to avoid bundling
them.

Prerequisites
-------------

MetaPkg requires Python 3.7+.

License
-------

Apache 2.0.
