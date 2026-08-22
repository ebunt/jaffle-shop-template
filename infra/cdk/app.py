#!/usr/bin/env python3
import os

import aws_cdk as cdk

from jaffle_shop_infra.stack import JaffleShopStack

app = cdk.App()

JaffleShopStack(
    app,
    "JaffleShopStack",
    env=cdk.Environment(
        account=os.environ.get("CDK_DEFAULT_ACCOUNT"),
        region=os.environ.get("CDK_DEFAULT_REGION", "us-east-1"),
    ),
)

app.synth()
