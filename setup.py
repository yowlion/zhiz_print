from setuptools import setup, find_packages

with open('requirements.txt') as f:
    requirements = f.read().splitlines()

setup(
    name='zhiz_print',
    version='15.22.05',
    description='Zhiz Print - Advanced WYSIWYG print designer app for ERPNext/Frappe, specialized for complex print format design (multi-page grid, cell merging, expressions, barcode/QR, multi-engine PDF export). By Guangde Zhizhao Technology Co., Ltd.',
    long_description='Owned and maintained by Guangde Zhizhao Technology Co., Ltd. (广德智兆科技有限公司), actively developed by Zhi Zhao Huang (智兆.黄). Official source: https://gitee.com/gdzhiz/zhiz_print',
    author='Zhi Zhao Huang (智兆.黄), Guangde Zhizhao Technology Co., Ltd.',
    author_email='hyowlion@gmail.com',
    url='https://gitee.com/gdzhiz/zhiz_print',
    packages=find_packages(),
    zip_safe=False,
    include_package_data=True,
    install_requires=requirements
)
