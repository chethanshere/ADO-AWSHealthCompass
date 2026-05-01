# AWS Health Compass - BMC Helix ITSM Integration

**AWS Health Compass BMC Helix Integration** is a serverless solution that converts AWS Health planned lifecycle events into actionable incidents in BMC Helix ITSM. It automates incident creation and management, ensuring resource owners are notified about relevant infrastructure changes through configurable routing capabilities.

## Key Features

1. **Automated Incident Management**: Automatically creates BMC Helix ITSM incidents for AWS Health planned lifecycle events, eliminating manual monitoring and ticket creation.

2. **Event-Driven Architecture**: Serverless, event-driven design that processes events in near real-time with minimal operational overhead.

3. **Flexible Routing Models**: Three deployment models for routing incidents:
   - **Account-based routing**: Routes incidents based on affected AWS accounts
   - **Service-based routing**: Routes incidents by AWS service type (EC2, S3, etc.)
   - **Tag-based routing**: Routes incidents based on resource tags for team-specific notifications

4. **Intelligent Incident Updates**: Updates existing incidents when new resources are affected by the same AWS Health event, preventing duplicates.

5. **Cross-Organization Visibility**: Aggregates health events across all accounts in an AWS Organization.

6. **Resilient Message Processing**: Dead letter queues with configurable retry policies ensure no events are lost.

7. **Secure Credential Management**: Uses AWS Secrets Manager for BMC Helix credentials with JWT token-based authentication.

## Architecture

The solution consists of the following components:

1. **AWS Health Events**: Supports single account or event aggregation across Organization using AWS Health's organizational view with delegated account feature.

2. **AWS EventBridge**: Custom EventBridge bus aggregates and routes AWS Health planned lifecycle events across an organization.

3. **AWS Lambda Functions**:
   - **HealthEventProcessorLambda**: Processes incoming AWS Health events, categorizes resources, and prepares messages for incident creation or updates
   - **HealthEventBMCHelixIntegration**: Creates and updates BMC Helix ITSM incidents based on processed AWS Health events

4. **SQS Queues**: Provides buffering and resilience between Lambda components with Dead Letter Queue (DLQ) implementation.

5. **AWS Secrets Manager**: Securely stores BMC Helix credentials (username and password).

6. **DynamoDB Tables**:
   - **Track Table**: Maintains the relationship between AWS Health events, affected resources, and their corresponding BMC Helix incidents
   - **Mapping Table**: Maps AWS identifiers to BMC Helix assignment groups and categories. Variants:
     - `AccountBMCHelixTable`: Maps AWS account IDs to assignment groups
     - `ServiceBMCHelixTable`: Maps AWS service names to assignment groups
     - `TagBMCHelixTable`: Maps resource tag values to assignment groups
     - Includes a `DefaultProjectCode` entry for unmapped resources

7. **IAM Roles and Policies**: Least-privilege roles for Lambda execution and cross-account access (Tag model only).

## Prerequisites

1. **AWS Health Organizational View**: Enable [AWS Health organizational view](https://docs.aws.amazon.com/health/latest/ug/enable-organizational-view.html) and [AWS Health delegated account](https://docs.aws.amazon.com/health/latest/ug/delegated-administrator-organizational-view.html).

2. **Deployment Account**: Deploy the solution in the AWS Health delegated account (referenced as `deployment-account`).

3. **BMC Helix ITSM Instance**:
   - BMC Helix ITSM instance URL (e.g., `https://your-instance.helixitsm.bmc.com`)
   - Username with permissions to create and update incidents
   - Password
   - The user must have access to the `HPD:IncidentInterface_Create` and `HPD:WorkLog` forms

4. **S3 Bucket**: S3 bucket for Lambda deployment packages in the deployment account.

5. **Cross-Account Role** (Tag deployment model only):
   - IAM Role name for cross-account access to linked accounts
   - Tag key to monitor for routing events

## Deployment Instructions

### 1. Prepare Lambda Packages

```bash
zip -r HealthEventProcessorLambda.zip HealthEventProcessorLambda.py
zip -r HealthEventBMCHelixIntegration.zip HealthEventBMCHelixIntegration.py
```

### 2. Upload to S3

1. Login to your `deployment-account`
2. Switch to your preferred deployment region
3. Upload the Lambda zip files to your S3 bucket

### 3. Deploy CloudFormation Template

1. Open AWS CloudFormation console
2. Select "Create Stack" → "With new resources (standard)"
3. Choose "Upload a template file" → Select `cloudformation.yaml`
4. Click "Next"
5. Provide the following parameters:

#### Required Parameters

| Parameter | Description | Example |
|-----------|-------------|---------|
| **Stack name** | Name for your CloudFormation stack | `aws-health-bmchelix-integration` |
| **DeployModel** | Deployment model (Account/Service/Tag) | `Account` |
| **BMCHelixUrl** | BMC Helix ITSM instance URL | `https://your-instance.helixitsm.bmc.com` |
| **BMCHelixUsername** | BMC Helix username | `integration.user` |
| **BMCHelixPassword** | BMC Helix password | `your-password` |
| **S3BucketName** | S3 bucket containing Lambda packages | `my-lambda-deployment-bucket` |
| **HealthEventProcessorLambdaKey** | S3 key for processor Lambda | `HealthEventProcessorLambda.zip` |
| **HealthEventBMCHelixIntegrationLambdaKey** | S3 key for BMC Helix Lambda | `HealthEventBMCHelixIntegration.zip` |

#### Conditional Parameters (Tag Model Only)

| Parameter | Description | Example |
|-----------|-------------|---------|
| **AssumeRoleName** | IAM role name for cross-account access | `HealthEventTagRole` |
| **TagKey** | Tag key to monitor for routing | `Environment` |

6. Click "Next" → Configure stack options → Click "Next"
7. Review configuration and select "I acknowledge that AWS CloudFormation might create IAM resources"
8. Click "Create stack"

### 4. Monitor Deployment

Monitor stack creation progress in the CloudFormation console. Once complete, note the outputs for resource details.

## Configuration

### Configure Health Event Aggregation

Create rules to send AWS Health events from default EventBridge to the custom EventBridge bus:

1. In your deployment account and region, find the custom EventBridge bus `EventBridgeRuleName` from CloudFormation outputs. Note the custom Event bus ARN.
2. Go to EventBridge console → Event buses → Select default Event bus → Create rule
3. Give a name, ensure rule is enabled, choose "Rule with an event pattern" and click Next
4. For Event source, select "AWS events or EventBridge partner events"
5. For Event pattern, select "Use pattern form"
6. Event source → AWS service, AWS Service → Health, Event type → All events. Click Next.
7. In Target1 → select EventBridge event bus → select same account/region or cross-account option as appropriate
8. For Event bus target → select the custom event bus noted in step 1
9. Select "Create a new role for the specific resource", click Next
10. Click Next and then Create rule
11. Repeat for all regions to forward events to the custom Event bus

### Configure DynamoDB Mapping

Configure the DynamoDB mapping table based on your chosen deployment model.

1. Locate the DynamoDB table `DynamoDBMappingTable` from CloudFormation outputs
2. Access the DynamoDB console and select your table
3. Use the PartiQL editor or item creation interface to add mapping entries
4. Always create a `DefaultProjectCode` entry to handle unmapped resources

#### Account Model

```sql
-- Default mapping (catches all for unmapped accounts)
INSERT into "AccountBMCHelixTable" value {  
    'Account': 'DefaultProjectCode',
    'ACBMCHelixAssignmentGroup': 'Cloud Operations',
    'ACBMCHelixCategory': 'AWS'
}

-- Account-specific mapping
INSERT into "AccountBMCHelixTable" value {  
    'Account': '123456789012',
    'ACBMCHelixAssignmentGroup': 'Production Support',
    'ACBMCHelixCategory': 'AWS Infrastructure'
}
```

#### Service Model

```sql
-- Default mapping (catches all for unmapped services)
INSERT into "ServiceBMCHelixTable" value {  
    'Service': 'DefaultProjectCode',
    'SBMCHelixAssignmentGroup': 'Cloud Operations',
    'SBMCHelixCategory': 'AWS'
}

-- Service-specific mapping
INSERT into "ServiceBMCHelixTable" value {  
    'Service': 'EC2',
    'SBMCHelixAssignmentGroup': 'Compute Team',
    'SBMCHelixCategory': 'AWS Compute'
}
```

#### Tag Model

```sql
-- Default mapping (catches all for unmapped tag values)
INSERT into "TagBMCHelixTable" value {  
    'HostTag': 'DefaultProjectCode',
    'HTBMCHelixAssignmentGroup': 'Cloud Operations',
    'HTBMCHelixCategory': 'AWS'
}

-- Tag-specific mapping
INSERT into "TagBMCHelixTable" value {  
    'HostTag': 'production-web',
    'HTBMCHelixAssignmentGroup': 'Web Platform Team',
    'HTBMCHelixCategory': 'AWS Web Infrastructure'
}
```

> **Note**: Replace table names, assignment groups, and categories with your actual BMC Helix values.

### Cross-Account IAM Role Setup (Tag Model Only)

Create the following role in every linked account:

**Role Permission Policy:**
```json
{
    "Version": "2012-10-17",
    "Statement": [
        {
            "Effect": "Allow",
            "Action": [
                "tag:GetResources",
                "tag:GetTagKeys",
                "tag:GetTagValues"
            ],
            "Resource": "*"
        },
        {
            "Effect": "Allow",
            "Action": [
                "s3:GetBucketTagging",
                "iam:ListRoleTags",
                "iam:ListUserTags",
                "route53:ListTagsForResource",
                "autoscaling:DescribeTags"
            ],
            "Resource": "*"
        }
    ]
}
```

**Role Trust Policy:**
```json
{
    "Version": "2012-10-17",
    "Statement": [
        {
            "Effect": "Allow",
            "Principal": {
                "AWS": "arn:aws:iam::<deployment-account-id>:role/<health-event-processor-lambda-role>"
            },
            "Action": "sts:AssumeRole"
        }
    ]
}
```

> **Note**: The processor role ARN can be found in the `HealthEventProcessorRoleArn` CloudFormation output.

## BMC Helix ITSM API Details

### Authentication

The solution uses JWT token-based authentication:
1. Obtains a JWT token via `POST /api/jwt/login`
2. Uses the token in `Authorization: AR-JWT <token>` header for all API calls
3. Releases the token via `POST /api/jwt/logout` after processing

### Incident Creation

Incidents are created via the `HPD:IncidentInterface_Create` form:
- **Description**: Summary based on deployment model and event type
- **Detailed_Decription**: Event description and affected resource details
- **Impact**: 3-Moderate/Limited
- **Urgency**: 3-Medium
- **Status**: New
- **Assigned Group**: From DynamoDB mapping table
- **Categorization Tier 1**: From DynamoDB mapping table

### Incident Updates

Updates are added as work log entries via the `HPD:WorkLog` form with resource status details.

## Testing the Solution

### 1. Verify Deployment

Check CloudFormation stack outputs for:
- DynamoDB table names
- Lambda function ARNs
- SQS queue URLs
- BMC Helix secret name

### 2. Test BMC Helix Connectivity

Verify BMC Helix credentials by testing JWT token acquisition:
```bash
curl -X POST "https://your-instance.helixitsm.bmc.com/api/jwt/login" \
     -H "Content-Type: application/json" \
     -d '{"username":"your-user","password":"your-password"}'
```

### 3. Monitor Processing

1. **CloudWatch Logs**: Review Lambda execution logs
2. **SQS Queues**: Check for messages in processing queues
3. **DLQ**: Monitor dead letter queues for failed messages
4. **BMC Helix**: Verify incidents are created/updated

## Troubleshooting

### Common Issues

1. **JWT Authentication Failures**:
   - Verify BMC Helix credentials in Secrets Manager
   - Check BMC Helix user account is active
   - Ensure the user has API access permissions
   - Test JWT login endpoint manually

2. **Incident Creation Failures**:
   - Verify the user has permissions on `HPD:IncidentInterface_Create`
   - Check assignment group names match exactly in BMC Helix
   - Review CloudWatch Logs for API response details

3. **Missing Incidents**:
   - Check DynamoDB mapping entries are correct
   - Verify SQS queues for stuck messages
   - Confirm EventBridge rules are properly configured

4. **Cross-Account Tag Discovery Issues** (Tag Model):
   - Verify IAM roles in all linked accounts
   - Check trust relationships
   - Ensure tag permissions are configured

### Debugging Commands

```bash
# Check CloudWatch Logs
aws logs describe-log-groups --log-group-name-prefix "/aws/lambda/your-stack-name"

# Monitor SQS Queues
aws sqs get-queue-attributes --queue-url <queue-url> --attribute-names All

# Verify DynamoDB Records
aws dynamodb scan --table-name <tracking-table-name>

# Test BMC Helix JWT Login
curl -X POST "https://your-instance.helixitsm.bmc.com/api/jwt/login" \
     -H "Content-Type: application/json" \
     -d '{"username":"user","password":"pass"}'
```

## Security

- BMC Helix credentials are stored securely in AWS Secrets Manager
- JWT tokens are obtained per-invocation and released after use
- IAM roles follow least privilege principle
- DynamoDB tables are encrypted at rest (SSE enabled)
- Consider implementing VPC endpoints for enhanced security
- Regularly rotate BMC Helix credentials

## License

This library is licensed under the MIT-0 License. See the LICENSE file.
