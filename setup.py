from setuptools import setup, find_packages

with open('requirements.txt') as f:
    requirements = f.read().splitlines()

setup(
    name='zhiz_print',
    version='15.01.01',
    description='Frappe Advanced Print Designer Module',
    author='yowlion',
    author_email='hyowlion@email.com',
    packages=find_packages(),
    zip_safe=False,
    include_package_data=True,
    install_requires=requirements
)
