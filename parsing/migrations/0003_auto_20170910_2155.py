# -*- coding: utf-8 -*-
# Generated by Django 1.9.4 on 2017-09-11 02:55
from __future__ import unicode_literals

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("parsing", "0002_auto_20170803_2329"),
    ]

    operations = [
        migrations.AlterField(
            model_name="dataupdate",
            name="reason",
            field=models.CharField(default="Scheduled Update", max_length=200),
        ),
        migrations.AlterField(
            model_name="dataupdate",
            name="update_type",
            field=models.CharField(
                choices=[
                    ("C", "courses"),
                    ("T", "textbooks"),
                    ("E", "evaluations"),
                    ("M", "miscellaneous"),
                ],
                default="M",
                max_length=1,
            ),
        ),
    ]
