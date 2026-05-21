import aws_cdk as cdk

from stacks.trustgraph_worker_stack import TrustGraphWorkerStack

app = cdk.App()

TrustGraphWorkerStack(
    app,
    "TrustGraphWorkerStack",
    env=cdk.Environment(
        account="742543724243",
        region="us-east-1",
    ),
)

app.synth()
