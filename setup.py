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
    python_requires=">=3.9",
    install_requires=[
        "distro~=1.7.0",
        "requests~=2.27.0",
        "poetry @ git+https://github.com/python-poetry/poetry.git#ceb358685dec445ff995c4e1e6eec8ba4b1346ab",
        "distlib~=0.3.4",
        'python-magic~=0.4.26; platform_system=="Linux"',
        'python-magic-bin~=0.4.14; platform_system!="Linux"',
        "wheel>=0.32.3",
        "setuptools-rust>=0.11.4",
        "tomli>=1.2",
    ],
    extras_require={
        "test": [
            "types-requests~=2.25.9",
        ]
    },
)
