#!/usr/bin/env python3

import kopf
import kubernetes.client
from kubernetes.client.rest import ApiException
import time
import logging


logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


try:
    kubernetes.config.load_incluster_config()
except:
    kubernetes.config.load_kube_config()


@kopf.on.create('otus.homework', 'v1', 'mysqls')
def create_mysql_instance(body, spec, name, namespace, patch, **kwargs):
    logger.info(f"Creating MySQL instance: {name} in namespace: {namespace}")

    image = spec.get('image', 'mysql:8.0')
    database = spec.get('database', 'mydatabase')
    password = spec.get('password', 'defaultpassword')
    storage_size = spec.get('storage_size', '10Gi')

    logger.info(f"Params: image={image}, db={database}, storage={storage_size}")

    # Create resources
    try:
        # Create PV
        pv_name = f"{name}-pv"
        create_persistent_volume(pv_name, storage_size, namespace)

        # Create PVC
        pvc_name = f"{name}-pvc"
        create_persistent_volume_claim(pvc_name, storage_size,  namespace, pv_name, body)

        # Create Secret
        secret_name = f"{name}-secret"
        create_secret(secret_name, password, namespace, body)

        # Create Deployment
        create_deployment(name, image, database, secret_name, pvc_name, namespace, body)

        # Create Service
        service_name = f"{name}-service"
        create_service(service_name, name, namespace, body)

        logger.info(f"MySQL instance {name} created successfully!")

        return {
            'status': 'created',
            'message': 'MySQL instance created successfully',
            'components': {
                'pv': pv_name,
                'pvc': pvc_name,
                'secret': secret_name,
                'deployment': name,
                'service': service_name
            }
        }

    except Exception as e:
        logger.error(f"Failed to create MySQL instance: {str(e)}")
        raise kopf.TemporaryError(
            f"Failed to create MySQL instance: {str(e)}",
            delay=30
        )


def create_persistent_volume(name, storage_size, namespace):
    logger.info(f"CreatingPersistentVolume: {name}")

    api = kubernetes.client.CoreV1Api()

    pv_body = {
        'apiVersion': 'v1',
        'kind': 'PersistentVolume',
        'metadata': {
            'name': name,
            'labels': {
                'type': 'local',
                'app': 'mysql',
                'pv-name': name
            }
        },
        'spec': {
            'storageClassName': 'manual',
            'capacity': {
                'storage': storage_size
            },
            'accessModes': ['ReadWriteOnce'],
            'hostPath': {
                'path': f'/data/{namespace}/{name}'
            },
            'persistentVolumeReclaimPolicy': 'Retain'
        }
    }

    try:
        api.create_persistent_volume(body=pv_body)
        logger.info(f"ersistentVolume {name} created!")
    except ApiException as e:
        if e.status == 409:  # Already exists
            logger.warning(f"PersistentVolume {name} already exists")
        else:
            raise

def create_persistent_volume_claim(name, storage_size, namespace, pv_name, owner_body):
    logger.info(f"Creating PersistentVolumeClaim: {name}")

    api = kubernetes.client.CoreV1Api()

    pvc_body = {
        'apiVersion': 'v1',
        'kind': 'PersistentVolumeClaim',
        'metadata': {
            'name': name,
            'namespace': namespace,
            'labels': {
                'app': 'mysql',
                'pvc-name': name
            }
        },
        'spec': {
            'storageClassName': 'standard',
            'accessModes': ['ReadWriteOnce'],
            'resources': {
                'requests': {
                    'storage': storage_size
                }
            },
            'selector': {
                'matchLabels': {
                    'pv-name': pv_name
                }
            }
        }
    }

    # Add owner
    kopf.adopt(pvc_body, owner=owner_body)

    try:
        api.create_namespaced_persistent_volume_claim(namespace, pvc_body)
        logger.info(f"PersistentVolumeClaim {name} created successfully!")
    except ApiException as e:
        if e.status == 409:
            logger.warning(f"PersistentVolumeClaim {name} already exists")
        else:
            raise


def create_secret(name, password, namespace, owner_body):
    logger.info(f"Creating Secret: {name}")

    api = kubernetes.client.CoreV1Api()

    secret_body = {
        'apiVersion': 'v1',
        'kind': 'Secret',
        'metadata': {
            'name': name,
            'namespace': namespace,
            'labels': {
                'app': 'mysql',
                'secret-name': name
            }
        },
        'type': 'Opaque',
        'stringData': {
            'password': password
        }
    }

    # Add owner
    kopf.adopt(secret_body, owner=owner_body)

    try:
        api.create_namespaced_secret(namespace, secret_body)
        logger.info(f"Secret {name} created successfully!")
    except ApiException as e:
        if e.status == 409:
            logger.warning(f"Secret {name} already exists")
        else:
            raise


def create_deployment(name, image, database, secret_name, pvc_name, namespace, owner_body):
    logger.info(f"Creating Deployment: {name}")

    api = kubernetes.client.AppsV1Api()

    deployment_body = {
        'apiVersion': 'apps/v1',
        'kind': 'Deployment',
        'metadata': {
            'name': name,
            'namespace': namespace,
            'labels': {
                'app': 'mysql',
                'deployment-name': name
            }
        },
        'spec': {
            'replicas': 1,
            'selector': {
                'matchLabels': {
                    'app': 'mysql',
                    'instance': name
                }
            },
            'template': {
                'metadata': {
                    'labels': {
                        'app': 'mysql',
                        'instance': name
                    }
                },
                'spec': {
                    'containers': [{
                        'name': 'mysql',
                        'image': image,
                        'env': [
                            {
                                'name': 'MYSQL_ROOT_PASSWORD',
                                'valueFrom': {
                                    'secretKeyRef': {
                                        'name': secret_name,
                                        'key': 'password'
                                    }
                                }
                            },
                            {
                                'name': 'MYSQL_DATABASE',
                                'value': database
                            }
                        ],
                        'ports': [{
                            'containerPort': 3306,
                            'name': 'mysql'
                        }],
                        'volumeMounts': [{
                            'name': 'mysql-data',
                            'mountPath': '/var/lib/mysql'
                        }],
                    }],
                    'volumes': [{
                        'name': 'mysql-data',
                        'persistentVolumeClaim': {
                            'claimName': pvc_name
                        }
                    }]
                }
            }
        }
    }

    # Add owner
    kopf.adopt(deployment_body, owner=owner_body)

    try:
        api.create_namespaced_deployment(namespace, deployment_body)
        logger.info(f"Deployment {name} created successfully!")
    except ApiException as e:
        if e.status == 409:
            logger.warning(f"Deployment {name} already exists")
        else:
            raise


def create_service(name, deployment_name, namespace, owner_body):
    logger.info(f"Creating Service: {name}")

    api = kubernetes.client.CoreV1Api()

    service_body = {
        'apiVersion': 'v1',
        'kind': 'Service',
        'metadata': {
            'name': name,
            'namespace': namespace,
            'labels': {
                'app': 'mysql',
                'service-name': name
            }
        },
        'spec': {
            'selector': {
                'app': 'mysql',
                'instance': deployment_name
            },
            'ports': [{
                'port': 3306,
                'targetPort': 3306,
                'protocol': 'TCP'
            }],
            'type': 'ClusterIP'
        }
    }

    # Add owner
    kopf.adopt(service_body, owner=owner_body)

    try:
        api.create_namespaced_service(namespace, service_body)
        logger.info(f"Service {name} created successfully!")
    except ApiException as e:
        if e.status == 409:
            logger.warning(f"Service {name} already exists")
        else:
            raise


@kopf.on.delete('otus.homework', 'v1', 'mysqls')
def delete_mysql_instance(name, namespace, **kwargs):
    logger.info(f"Start deleting MySQL: {name} ? namespace: {namespace}")


    resources_to_delete = [
        ('Service', f"{name}-service", namespace),
        ('Deployment', name, namespace),
        ('Secret', f"{name}-secret", namespace),
        ('PersistentVolumeClaim', f"{name}-pvc", namespace),
        ('PersistentVolume', f"{name}-pv", None)
    ]

    for resource_type, resource_name, resource_namespace in resources_to_delete:
        try:
            delete_resource(resource_type, resource_name, resource_namespace)
        except Exception as e:
            logger.warning(f"Failed to delete {resource_type} {resource_name}: {str(e)}")

    logger.info(f"MySQL instance {name} has been deleted")

    return {'message': f'MySQL instance {name} has been deleted'}


def delete_resource(resource_type, name, namespace):
    if resource_type == 'Deployment':
        api = kubernetes.client.AppsV1Api()
        api.delete_namespaced_deployment(name, namespace)
        logger.info(f"Deployment: {name} has been deleted")

    elif resource_type == 'Service':
        api = kubernetes.client.CoreV1Api()
        api.delete_namespaced_service(name, namespace)
        logger.info(f" Service: {name} has been deleted")

    elif resource_type == 'Secret':
        api = kubernetes.client.CoreV1Api()
        api.delete_namespaced_secret(name, namespace)
        logger.info(f"Secret: {name} has been deleted")

    elif resource_type == 'PersistentVolumeClaim':
        api = kubernetes.client.CoreV1Api()
        api.delete_namespaced_persistent_volume_claim(name, namespace)
        logger.info(f"PVC: {name} has been deleted")

    elif resource_type == 'PersistentVolume':
        api = kubernetes.client.CoreV1Api()
        api.delete_persistent_volume(name)
        logger.info(f"PV: {name} has been deleted")


@kopf.timer('otus.homework', 'v1', 'mysqls', interval=60.0)
def monitor_mysql_status(body, name, namespace, patch, **kwargs):
    try:
        api = kubernetes.client.AppsV1Api()
        deployment = api.read_namespaced_deployment(name, namespace)

        ready = deployment.status.ready_replicas or 0
        total = deployment.status.replicas or 0

        status = 'Running' if ready > 0 else 'Pending'

        logger.debug(f"Status MySQL {name}: {status} ({ready}/{total} pods)")

        return {
            'status': status,
            'readyReplicas': ready,
            'totalReplicas': total,
            'lastChecked': time.strftime('%Y-%m-%d %H:%M:%S')
        }

    except ApiException as e:
        if e.status == 404:
            return {'status': 'NotFound', 'lastChecked': time.strftime('%Y-%m-%d %H:%M:%S')}
        logger.error(f"Monitoring error: {str(e)}")
        return {'status': 'Error', 'error': str(e)}


if __name__ == '__main__':
    logger.info("MySQL Operator is running...")
    kopf.run()