from setuptools import setup


setup(
    setup_requires=["setuptools_scm"],
    use_scm_version=True,
    name="metapkg",
    description="Cross-Platform Meta Packaging System",
    author="MagicStack Inc.",
    author_email="hello@magic.io",
    packages=["metapkg"],
    include_package_data=True,
    entry_points={
        "console_scripts": [
            "metapkg = metapkg.app:main",
        ]
    },
    install_requires=[
        "distro~=1.5.0",
        "requests~=2.26.0",
        "poetry~=1.2.0a1",
        "toml~=0.10.2",
        "distlib~=0.3.2",
        "wheel>=0.32.3",
        "setuptools-rust>=0.11.4",
        "tomli>=1.2",
    ],
)
