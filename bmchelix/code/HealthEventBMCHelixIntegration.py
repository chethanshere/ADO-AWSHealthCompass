"""
AWS Health Compass - BMC Helix ITSM Integration Lambda
Creates and updates BMC Helix ITSM incidents based on processed AWS Health events.
Uses BMC Helix REST API for incident management.
"""

import json
import os
import logging
import sys
import urllib3
import boto3
from datetime import datetime
from botocore.exceptions import ClientError

# Configure logging
logger = logging.getLogger('bmchelix_lambda')
logger.setLevel(logging.INFO)
console_handler = logging.StreamHandler(sys.stdout)
console_handler.setLevel(logging.INFO)
formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
console_handler.setFormatter(formatter)
logger.addHandler(console_handler)

http = urllib3.PoolManager()

bmchelix_url = os.environ.get('BMCHELIX_URL')
if not bmchelix_url:
    raise ValueError("BMCHELIX_URL environment variable is not set")
# Strip trailing slash
bmchelix_url = bmchelix_url.rstrip('/')

# Setup boto3 session
session = boto3.session.Session()

# Setup DynamoDB tracking table
track_table_name = os.environ.get('DYNAMODB_TRACK_TABLE')
if not track_table_name:
    raise ValueError("DYNAMODB_TRACK_TABLE environment variable is not set")

dynamodb = boto3.resource('dynamodb')
track_table = dynamodb.Table(track_table_name)
logger.info(f"DynamoDB tracking table status: {track_table.table_status}")


def get_secret():
    """Retrieve BMC Helix credentials from Secrets Manager"""
    secret_name = os.environ.get('BMCHELIX_SECRET_NAME')
    if not secret_name:
        raise ValueError("BMCHELIX_SECRET_NAME environment variable is not set")

    region_name = os.environ.get('AWS_REGION', 'us-east-1')
    client = session.client(service_name='secretsmanager', region_name=region_name)

    try:
        response = client.get_secret_value(SecretId=secret_name)
    except ClientError as e:
        raise e

    secret = json.loads(response['SecretString'])
    return secret


def get_jwt_token(bmchelix_secret):
    """
    Authenticate with BMC Helix and obtain a JWT token.
    BMC Helix ITSM uses token-based authentication via the JWT login endpoint.

    Args:
        bmchelix_secret: Dictionary containing 'username' and 'password'

    Returns:
        str: JWT token for subsequent API calls
    """
    token_url = f"{bmchelix_url}/api/jwt/login"
    payload = json.dumps({
        "username": bmchelix_secret['username'],
        "password": bmchelix_secret['password']
    })
    headers = {
        'Content-Type': 'application/json'
    }

    response = http.request('POST', token_url, headers=headers, body=payload)

    if response.status == 200:
        token = response.data.decode('utf-8')
        logger.info("Successfully obtained JWT token from BMC Helix")
        return token
    else:
        error_msg = f"Failed to obtain JWT token. Status: {response.status}, Response: {response.data.decode()}"
        logger.error(error_msg)
        raise Exception(error_msg)


def get_bmchelix_headers(token):
    """Build HTTP headers for BMC Helix REST API using JWT token"""
    return {
        'Content-Type': 'application/json',
        'Authorization': f'AR-JWT {token}'
    }


def check_tracking_table(event_arn, resource_arn):
    """Check if a resource is already being tracked for a specific event"""
    try:
        response = track_table.get_item(
            Key={
                'resourceArn': resource_arn,
                'eventArn': event_arn
            }
        )
        if 'Item' in response:
            logger.info(f"Found tracking for resource {resource_arn} in event {event_arn}")
            return response['Item']
        logger.info(f"No tracking found for resource {resource_arn} in event {event_arn}")
        return None
    except ClientError as e:
        logger.error(f"Error querying tracking table: {e.response['Error']['Message']}")
        return None


def find_existing_incident_for_event(event_arn, identifier):
    """Find an existing BMC Helix incident ID for an event and identifier"""
    try:
        response = track_table.query(
            IndexName='TETkeyIndex',
            KeyConditionExpression='eventArn = :event_arn',
            ExpressionAttributeValues={
                ':event_arn': event_arn
            }
        )

        for item in response.get('Items', []):
            if 'bmcHelixIncidentId' in item:
                incident_id = item['bmcHelixIncidentId']
                logger.info(f"Found existing incident {incident_id} for event {event_arn}")
                return incident_id

        logger.info(f"No existing incident found for event {event_arn}")
        return None

    except ClientError as e:
        logger.error(f"Error querying tracking table for existing incident: {e.response['Error']['Message']}")
        return None


def store_event_tracking(event_arn, start_time, incident_id, resource_arn, incident_number=None):
    """Store event tracking information in DynamoDB"""
    from dateutil.relativedelta import relativedelta

    try:
        if isinstance(start_time, str):
            try:
                start_time_format = datetime.strptime(start_time, "%a, %d %b %Y %H:%M:%S %Z")
            except ValueError:
                try:
                    start_time_format = datetime.strptime(start_time, "%Y-%m-%dT%H:%M:%S.%fZ")
                except ValueError:
                    start_time_format = datetime.now()
        else:
            start_time_format = datetime.now()

        expiration_time = int((start_time_format + relativedelta(years=2)).timestamp())

        item = {
            'eventArn': event_arn,
            'resourceArn': resource_arn,
            'bmcHelixIncidentId': incident_id,
            'expirationTime': expiration_time
        }

        if incident_number:
            item['incidentNumber'] = incident_number

        track_table.put_item(Item=item)
        logger.info(f"Successfully stored event tracking for resource: {resource_arn} with incident ID: {incident_id}")
        return True

    except Exception as e:
        logger.error(f"Error storing event tracking: {str(e)}")
        return False


def get_resource_arn(resource):
    """Extract resource ARN from the resource object"""
    if isinstance(resource.get('arn'), dict) and 'resource_arn' in resource['arn']:
        return resource['arn']['resource_arn']
    return resource.get('arn')


def get_assignment_group_and_category(event_body, identifier):
    """Look up BMC Helix assignment group and category from DynamoDB mapping table"""
    deploy_model = event_body['deployModel']
    mapping_table_name = os.environ.get('BMCHELIX_DYNAMODB_TABLE')
    if not mapping_table_name:
        raise ValueError("BMCHELIX_DYNAMODB_TABLE environment variable is not set")

    mapping_table = dynamodb.Table(mapping_table_name)

    config = {
        'Account': {'key': 'Account', 'group_attr': 'ACBMCHelixAssignmentGroup', 'category_attr': 'ACBMCHelixCategory'},
        'Service': {'key': 'Service', 'group_attr': 'SBMCHelixAssignmentGroup', 'category_attr': 'SBMCHelixCategory'},
        'Tag': {'key': 'HostTag', 'group_attr': 'HTBMCHelixAssignmentGroup', 'category_attr': 'HTBMCHelixCategory'}
    }

    model_config = config.get(deploy_model)
    if not model_config:
        raise ValueError(f"Invalid deploy model: {deploy_model}")

    # Look up identifier, fall back to DefaultProjectCode
    for lookup_key in [identifier, 'DefaultProjectCode']:
        try:
            response = mapping_table.get_item(Key={model_config['key']: lookup_key})
            if 'Item' in response:
                item = response['Item']
                assignment_group = item.get(model_config['group_attr'])
                category = item.get(model_config['category_attr'], 'AWS')
                logger.info(f"Found mapping for {lookup_key}: assignment_group={assignment_group}, category={category}")
                return assignment_group, category
        except ClientError as e:
            logger.error(f"Error looking up mapping for {lookup_key}: {e.response['Error']['Message']}")

    logger.error(f"No mapping found for identifier {identifier} or DefaultProjectCode")
    return None, None


def build_incident_payload(event_body, identifier, resources, assignment_group, category):
    """Build payload for creating a BMC Helix incident"""
    eventTypeCode = event_body['detail']['eventTypeCode']
    service = event_body['detail']['service']
    deployModel = event_body['deployModel']
    event_description = event_body['detail']['eventDescription']

    # Build summary based on deploy model
    if deployModel == 'Account':
        summary = f"Account: {identifier} - AWS {service} Planned Maintenance - {eventTypeCode}"
    elif deployModel == 'Tag':
        summary = f"Tag: {identifier} - AWS {service} Planned Maintenance - {eventTypeCode} - Tag Based"
    elif deployModel == 'Service':
        summary = f"Service: {identifier} - AWS {service} Planned Maintenance - {eventTypeCode} - Service Based"
    else:
        summary = f"AWS Planned Maintenance - {eventTypeCode}"

    # Build detailed notes with resource information
    notes = f"Event Description:\n{event_description}\n\nAffected Resources:\n\n"
    for resource in resources:
        resource_arn = get_resource_arn(resource)
        if resource_arn:
            status = resource.get('status', 'UNKNOWN')
            last_updated = resource.get('last_updated_time', 'UNKNOWN')
            notes += (
                f"Resource: {resource_arn}\n"
                f"Status: {status}\n"
                f"Last Updated: {last_updated}\n\n"
            )

    payload = {
        "values": {
            "Description": summary,
            "Detailed_Decription": notes,
            "Impact": "3-Moderate/Limited",
            "Urgency": "3-Medium",
            "Status": "New",
            "Reported Source": "Other",
            "Service_Type": "Infrastructure Event",
            "Categorization Tier 1": category
        }
    }

    if assignment_group:
        payload["values"]["Assigned Group"] = assignment_group

    return payload


def build_worklog_payload(resources):
    """Build work log payload for updating an existing incident"""
    notes = "Update for resources:\n\n"
    for resource in resources:
        resource_arn = get_resource_arn(resource)
        if resource_arn:
            status = resource.get('status', 'UNKNOWN')
            last_updated = resource.get('last_updated_time', 'UNKNOWN')
            notes += (
                f"Resource: {resource_arn}\n"
                f"Status: {status}\n"
                f"Last Updated: {last_updated}\n\n"
            )
    return {
        "values": {
            "Description": notes,
            "Detailed Description": notes,
            "Work Log Type": "General Information",
            "View Access": "Public",
            "Secure Work Log": "No"
        }
    }


def create_incident(headers, payload):
    """Create an incident in BMC Helix ITSM"""
    url = f"{bmchelix_url}/api/arsys/v1/entry/HPD:IncidentInterface_Create"
    logger.info("Creating incident in BMC Helix")

    response = http.request('POST', url, headers=headers, body=json.dumps(payload))

    if response.status in [200, 201]:
        data = json.loads(response.data)
        # BMC Helix returns the entry with values containing Incident Number and Request ID
        incident_number = data.get('values', {}).get('Incident Number', '')
        request_id = data.get('values', {}).get('Request ID', '')
        logger.info(f"Successfully created incident: {incident_number} with request ID: {request_id}")
        return data
    else:
        logger.error(f"Failed to create incident. Status: {response.status}, Response: {response.data.decode()}")
        return None


def update_incident(incident_id, headers, payload):
    """Update an existing incident in BMC Helix ITSM via work log"""
    url = f"{bmchelix_url}/api/arsys/v1/entry/HPD:WorkLog/{incident_id}"
    logger.info(f"Adding work log to incident {incident_id}")

    response = http.request('POST', url, headers=headers, body=json.dumps(payload))

    if response.status in [200, 201, 204]:
        logger.info(f"Successfully added work log to incident {incident_id}")
        return response
    else:
        logger.error(f"Failed to update incident {incident_id}. Status: {response.status}, Response: {response.data.decode()}")
        return None


def release_jwt_token(token):
    """Release the JWT token after use"""
    try:
        url = f"{bmchelix_url}/api/jwt/logout"
        headers = {
            'Content-Type': 'application/json',
            'Authorization': f'AR-JWT {token}'
        }
        response = http.request('POST', url, headers=headers)
        if response.status == 204:
            logger.info("Successfully released JWT token")
        else:
            logger.warning(f"JWT token logout returned status: {response.status}")
    except Exception as e:
        logger.warning(f"Error releasing JWT token: {str(e)}")


def lambda_handler(event, context):
    """Main Lambda handler"""

    # Parse SQS message
    event_body = json.loads(event['Records'][0]['body'])

    eventArn = event_body['detail']['eventArn']
    deployModel = event_body['deployModel']
    startTime = event_body['detail'].get('startTime', '')

    logger.info(f"Processing event: {eventArn} with deploy model: {deployModel}")

    # Get BMC Helix credentials and obtain JWT token
    bmchelix_secret = get_secret()
    token = get_jwt_token(bmchelix_secret)
    headers = get_bmchelix_headers(token)

    try:
        # Process untracked resources (new incidents)
        untracked_resources = event_body.get('untrackedResources', {})
        if untracked_resources is None or untracked_resources == []:
            untracked_resources = {}
        elif not isinstance(untracked_resources, dict):
            logger.warning(f"untrackedResources is not a dictionary: {type(untracked_resources)}. Converting to empty dict.")
            untracked_resources = {}

        for identifier, resources in untracked_resources.items():
            logger.info(f"Processing resources for {deployModel} {identifier} with {len(resources)} resources")

            # Look up assignment group and category
            assignment_group, category = get_assignment_group_and_category(event_body, identifier)
            if not assignment_group:
                logger.error(f"No assignment group mapping found for identifier {identifier}, skipping")
                continue

            # Check if we already have an incident for this event
            existing_incident_id = find_existing_incident_for_event(eventArn, identifier)

            if existing_incident_id:
                logger.info(f"Found existing incident {existing_incident_id} for event {eventArn}")

                # Add work log to existing incident
                worklog_payload = build_worklog_payload(resources)
                update_incident(existing_incident_id, headers, worklog_payload)

                # Track all resources
                for resource in resources:
                    resource_arn = get_resource_arn(resource)
                    if resource_arn:
                        store_event_tracking(eventArn, startTime, existing_incident_id, resource_arn)
            else:
                logger.info(f"No existing incident found for event {eventArn}, creating new incident")

                # Create BMC Helix incident
                payload = build_incident_payload(event_body, identifier, resources, assignment_group, category)
                response_data = create_incident(headers, payload)

                if response_data:
                    incident_number = response_data.get('values', {}).get('Incident Number', '')
                    request_id = response_data.get('values', {}).get('Request ID', '')
                    # Use Request ID as the primary tracking identifier
                    incident_id = request_id or incident_number

                    logger.info(f"Successfully created incident {incident_number} with ID {incident_id}")

                    # Track all resources against the incident
                    for resource in resources:
                        resource_arn = get_resource_arn(resource)
                        if resource_arn:
                            store_event_tracking(eventArn, startTime, incident_id, resource_arn, incident_number)
                        else:
                            logger.warning(f"Could not extract resource ARN from resource: {resource}")

        # Process tracked resources (updates to existing incidents)
        logger.info(f"Processing tracked resources for {deployModel} mode")

        tracked_resources = event_body.get('trackedResources', [])
        if tracked_resources:
            # Group resources by bmcHelixIncidentId
            incident_groups = {}
            for resource in tracked_resources:
                resource_arn = resource.get('arn')
                if not resource_arn:
                    logger.warning(f"Resource missing arn: {resource}")
                    continue

                tracking_info = check_tracking_table(eventArn, resource_arn)
                if tracking_info and 'bmcHelixIncidentId' in tracking_info:
                    incident_id = tracking_info['bmcHelixIncidentId']
                    if incident_id not in incident_groups:
                        incident_groups[incident_id] = []
                    incident_groups[incident_id].append(resource)
                else:
                    logger.warning(f"No tracking info found for resource: {resource_arn}")

            # Add work logs to each incident
            for incident_id, resources in incident_groups.items():
                logger.info(f"Updating incident {incident_id} with {len(resources)} resources")
                worklog_payload = build_worklog_payload(resources)
                update_incident(incident_id, headers, worklog_payload)

    finally:
        # Always release the JWT token
        release_jwt_token(token)


logger.info('Lambda function initialized')
