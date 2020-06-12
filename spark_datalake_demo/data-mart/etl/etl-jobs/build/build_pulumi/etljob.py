import pulumi, json, glob, os
from pulumi_aws import glue, iam, s3
from typing import List, Dict, Union, Callable


class GlueEtlJob(pulumi.ComponentResource):
    glue_job: glue.Job
    role: iam.Role

    def __init__(self, name,
            # optional list of iam.policy to attach to role
            iam_policies:List[str]=None,

            # encrypt logs
            security_config:glue.SecurityConfiguration=None,

            # required
            script_location:str=None,
            job_args:Dict[str,str]=None,
            job_db_connections:List[Union[str,glue.Connection]]=None,
            job_name_prefix:str='',
            target_table:str='',
            data_classification:str='nonsensitive',
            tags:Dict[str,str]=None,
            opts=None):
        super().__init__('hca:GlueEtlJob', name, None, opts)

        merged_tags = tags.copy() if tags else {}
        merged_tags.update({
            'hca:dataclassification': data_classification,
            'hca:job_name': name,
            'hca:target_table': target_table,
        })

        self.role = iam.Role(
            resource_name=f"{name}-role",
            path="/glue/",
            description=f"role for glue etl job => {name}",
            assume_role_policy=json.dumps({
                "Version": "2012-10-17",
                "Statement": [{
                    "Effect": "Allow",
                    "Action": "sts:AssumeRole",
                    "Principal": {
                        "Service": "glue.amazonaws.com"
                    }
                }]
            }),
            force_detach_policies=True,
            opts=pulumi.ResourceOptions(parent=self),
            tags=merged_tags)

        if iam_policies:
            for i, iam_policy in enumerate(iam_policies):
                iam.RolePolicyAttachment(f"{name}-policy-{i}",
                    policy_arn=iam_policy,
                    role=self.role,
                    opts=pulumi.ResourceOptions(parent=self))

        self.glue_job = glue.Job(f"{job_name_prefix}_{name}",
            glue_version='1.0',
            command={
                'name': 'glueetl',
                'pythonVersion': '3',
                'scriptLocation': script_location
            },
            role_arn=self.role.arn,
            default_arguments=job_args,
            max_capacity=2,
            security_configuration=security_config,
            tags=merged_tags,
            connections=job_db_connections,
            opts=pulumi.ResourceOptions(parent=self))


def etljob_policy(datalake_bucket_arn, scripts_bucket_arn):

    ###
    # etl job iam roles tagged with: 
    # hca:job_name: fund.fund
    # hca:target_table: fund
    ###
    return json.dumps({
        "Version": "2012-10-17",
        "Statement": [{
            # allow spark to write dumb _$folder$ files
            "Effect": "Allow",
            "Action": [
                "s3:PutObject",
                "s3:DeleteObject"
            ],
            "Resource": f"{datalake_bucket_arn}/*_$folder$"
        },{
            # allow delete
            "Effect": "Allow",
            "Action": [
                "s3:DeleteObject*",
            ],
            "Resource": [
                f"{datalake_bucket_arn}/raw/${{aws:PrincipalTag/hca:job_name}}/*",
                f"{datalake_bucket_arn}/mart/${{aws:PrincipalTag/hca:target_table}}/*",
                f"{datalake_bucket_arn}/mart/${{aws:PrincipalTag/hca:target_table}}.tmp.parquet/*",
            ]
        },{
            # allow put object to new files when hca:target_table on role = hca:target_table on object
            # or if object is not tagged
            "Effect": "Allow",
            "Action": [
                "s3:PutObject",
                "s3:PutObjectTagging",
                "s3:PutObjectVersionTagging"
            ],
            "Resource": [
                f"{datalake_bucket_arn}/mart/${{aws:PrincipalTag/hca:target_table}}/*",
                f"{datalake_bucket_arn}/mart/${{aws:PrincipalTag/hca:target_table}}.tmp.parquet/*",
                f"{datalake_bucket_arn}/archive/${{aws:PrincipalTag/hca:job_name}}/*",
                f"{datalake_bucket_arn}/delta/${{aws:PrincipalTag/hca:target_table}}/*",
            ],
            "Condition": {
                "StringEqualsIfExists": {
                    "s3:RequestObjectTag/hca:target_table": "${aws:PrincipalTag/hca:target_table}"
                }
            }
        },{
            # allow get object for tag-less and nonsensitive
            "Effect": "Allow",
            "Action": "s3:GetObject",
            "Resource": [
                datalake_bucket_arn,
                datalake_bucket_arn+'/*',
            ],
            "Condition": {
                "StringEqualsIfExists": {
                    "s3:ExistingObjectTag/hca:dataclassification": "nonsensitive"
                }
            }
        },{
            # allow get object for role=pii => tag=confidential
            "Effect": "Allow",
            "Action": "s3:GetObject",
            "Resource": [
                datalake_bucket_arn,
                datalake_bucket_arn+'/*',
            ],
            "Condition": {
                "StringEquals": {
                    "aws:PrincipalTag/hca:dataclassification": "pii",
                    "s3:ExistingObjectTag/hca:dataclassification": "confidential"

                }
            }
        },{
            # allow get object for role=tag
            "Effect": "Allow",
            "Action": "s3:GetObject",
            "Resource": [
                datalake_bucket_arn,
                datalake_bucket_arn+'/*',
            ],
            "Condition": {
                "StringEquals": {
                    "s3:ExistingObjectTag/hca:dataclassification": "${aws:PrincipalTag/hca:dataclassification}"
                }
            }
        },{
            # allow list whole bucket
            "Effect": "Allow",
            "Action": [
                "s3:GetBucketLocation",
                "s3:GetObjectTagging",
                "s3:ListBucket*",
                "s3:ListMultipartUploadParts",
                "s3:AbortMultipartUpload",
            ],
            "Resource": [
                datalake_bucket_arn,
                datalake_bucket_arn+'/*',
            ]
        },{
            # allow get glue connection (postgres)
            "Effect": "Allow",
            "Action": "glue:GetConnection",
            "Resource": "*"
        },{
            # allow encrypt cloudwatch logs with kms key
            "Effect": "Allow",
            "Action": [
                "logs:AssociateKmsKey"
            ],
            "Resource": "arn:aws:logs:*:*:log-group:/aws-glue/*"
        },{
            # allow get all from scripts bucket
            "Effect": "Allow",
            "Action": "s3:GetObject",
            "Resource": [
                scripts_bucket_arn,
                scripts_bucket_arn + '/*'
            ]
        }]
    })
                