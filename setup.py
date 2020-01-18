from setuptools import setup


setup(
    setup_requires=['setuptools_scm'],
    use_scm_version=True,
    name='metapkg',
    description='Cross-Platform Meta Packaging System',
    author='MagicStack Inc.',
    author_email='hello@magic.io',
    packages=['metapkg'],
    include_package_data=True,
    entry_points={
        'console_scripts': [
            'metapkg = metapkg.app:main',
        ]
    },
    install_requires=[
        'distro~=1.4.0',
        'requests~=2.19.0',
        'poetry~=0.11.2',
        'toml~=0.10.0',
        'distlib~=0.2.7',
        'wheel~=0.32.3',
    ],
)
