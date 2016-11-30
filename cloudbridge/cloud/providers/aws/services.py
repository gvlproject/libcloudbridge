"""Services implemented by the AWS provider."""
import time
import string

from boto.ec2.blockdevicemapping import BlockDeviceMapping
from boto.ec2.blockdevicemapping import BlockDeviceType
from botocore.exceptions import ClientError as EC2ResponseError

from cloudbridge.cloud.base.resources import ClientPagedResultList
from cloudbridge.cloud.base.resources import ServerPagedResultList
from cloudbridge.cloud.base.services import BaseBlockStoreService
from cloudbridge.cloud.base.services import BaseComputeService
from cloudbridge.cloud.base.services import BaseImageService
from cloudbridge.cloud.base.services import BaseInstanceService
from cloudbridge.cloud.base.services import BaseInstanceTypesService
from cloudbridge.cloud.base.services import BaseKeyPairService
from cloudbridge.cloud.base.services import BaseNetworkService
from cloudbridge.cloud.base.services import BaseObjectStoreService
from cloudbridge.cloud.base.services import BaseRegionService
from cloudbridge.cloud.base.services import BaseSecurityGroupService
from cloudbridge.cloud.base.services import BaseSecurityService
from cloudbridge.cloud.base.services import BaseSnapshotService
from cloudbridge.cloud.base.services import BaseSubnetService
from cloudbridge.cloud.base.services import BaseVolumeService
from cloudbridge.cloud.interfaces.resources import InstanceType
from cloudbridge.cloud.interfaces.resources \
    import InvalidConfigurationException
from cloudbridge.cloud.interfaces.resources import KeyPair
from cloudbridge.cloud.interfaces.resources import MachineImage
# from cloudbridge.cloud.interfaces.resources import Network
from cloudbridge.cloud.interfaces.resources import PlacementZone
from cloudbridge.cloud.interfaces.resources import SecurityGroup
from cloudbridge.cloud.interfaces.resources import Snapshot
from cloudbridge.cloud.interfaces.resources import Volume

import cloudbridge as cb

import requests

from .resources import AWSBucket
from .resources import AWSFloatingIP
from .resources import AWSInstance
from .resources import AWSInstanceType
from .resources import AWSKeyPair
from .resources import AWSLaunchConfig
from .resources import AWSMachineImage
from .resources import AWSNetwork
from .resources import AWSRegion
from .resources import AWSRouter
from .resources import AWSSecurityGroup
from .resources import AWSSnapshot
from .resources import AWSSubnet
from .resources import AWSVolume

# Uncomment to enable logging by default for this module
# cb.set_stream_logger(__name__)


class EC2ServiceFilter(object):
    '''
        Generic AWS EC2 service filter interface

    :param AWSCloudProvider provider: AWS EC2 provider interface
    :param str service: Name of the EC2 service to use
    :param BaseCloudResource cb_iface: CloudBridge class to use
    '''
    def __init__(self, provider, service, cb_iface):
        self.provider = provider
        self.service = getattr(self.provider.ec2_conn, service)
        self.iface = cb_iface

    def get(self, val, filter_name, wrapper=True):
        '''
            Returns a single resource by filter

        :param str val: Value to filter with
        :param str filter_name: Name of the filter to use
        :param bool wrapper: If True, wraps the resulting Boto
            object in a CloudBridge object
        :returns: Boto resource object or CloudBridge object or None
        '''
        try:
            objs = list(self.service.filter(Filters=[{
                'Name': filter_name,
                'Values': [val]
            }]).limit(1))
            obj = objs[0] if objs else None
            if wrapper:
                return self.iface(self.provider, obj) if obj else None
            return obj
        except EC2ResponseError:
            return None

    def list(self, limit=None, marker=None):
        '''Returns a list of resources'''
        try:
            objs = [self.iface(self.provider, obj)
                    for obj in self.service.limit(limit)]
        except EC2ResponseError:
            objs = list()
        return ClientPagedResultList(self.provider, objs,
                                     limit=limit, marker=marker)

    def find(self, val, filter_name, limit=None, marker=None):
        '''
            Returns a list of resources by filter

        :param str val: Value to filter with
        :param str filter_name: Name of the filter to use
        '''
        try:
            objs = [
                self.iface(self.provider, obj)
                for obj in self.service.filter(Filters=[{
                    'Name': filter_name,
                    'Values': [val]
                }])
            ]
        except EC2ResponseError:
            objs = list()
        return ClientPagedResultList(self.provider, objs,
                                     limit=limit, marker=marker)

    def create(self, method, **kwargs):
        '''
            Creates a resource

        :param str method: Service method to invoke
        :param object kwargs: Arguments to be passed as-is to
            the service method
        '''
        res = getattr(self.provider.ec2_conn, method)(**kwargs)
        return self.iface(self.provider, res) if res else None

    def delete(self, val, filter_name):
        '''
            Deletes a resource by filter

        :param str val: Value to filter with
        :param str filter_name: Name of the filter to use
        :returns: False on error, True if the resource
            does not exist or was deleted successfully
        '''
        res = self.get(val, filter_name, wrapper=False)
        if res:
            try:
                res.delete()
            except EC2ResponseError:
                return False
        return True

    def wait_for_create(self, val, filter_name, timeout=15):
        '''
            Simple test for resource creation

        :returns: True on success, False on timeout, None if the
            object does not implement the refresh() method.
        '''
        while timeout > 0:
            time.sleep(2)
            obj = self.get(val, filter_name)
            if obj:
                if hasattr(obj, 'refresh') and callable(obj.refresh):
                    obj.refresh()
                    return True
                else:
                    return None
            timeout = timeout - 1
        return False

    def wait_for_delete(self, val, filter_name, timeout=15):
        '''Simple test for resource deletion'''
        while timeout > 0:
            time.sleep(2)
            if not self.get(val, filter_name):
                return True
            timeout = timeout - 1
        return False


class AWSSecurityService(BaseSecurityService):

    def __init__(self, provider):
        super(AWSSecurityService, self).__init__(provider)

        # Initialize provider services
        self._key_pairs = AWSKeyPairService(provider)
        self._security_groups = AWSSecurityGroupService(provider)

    @property
    def key_pairs(self):
        """
        Provides access to key pairs for this provider.

        :rtype: ``object`` of :class:`.KeyPairService`
        :return: a KeyPairService object
        """
        return self._key_pairs

    @property
    def security_groups(self):
        """
        Provides access to security groups for this provider.

        :rtype: ``object`` of :class:`.SecurityGroupService`
        :return: a SecurityGroupService object
        """
        return self._security_groups


class AWSKeyPairService(BaseKeyPairService):

    def __init__(self, provider):
        super(AWSKeyPairService, self).__init__(provider)
        self.iface = EC2ServiceFilter(self.provider, 'key_pairs', AWSKeyPair)

    def get(self, key_pair_id):
        """Returns a key pair given its name"""
        return self.iface.get(key_pair_id, 'key-name')

    def list(self, limit=None, marker=None):
        """List all key pairs associated with this account"""
        return self.iface.list(limit=limit, marker=marker)

    def find(self, name, limit=None, marker=None):
        """Searches for a key pair by name"""
        return self.iface.find(name, 'key-name', limit=limit, marker=marker)

    def create(self, name):
        """Creates a new key pair"""
        return self.iface.create('create_key_pair', KeyName=name)

    def delete(self, key_pair_id):
        """Deletes a key pair by name"""
        return self.iface.delete(key_pair_id, 'key-name')


class AWSSecurityGroupService(BaseSecurityGroupService):

    def __init__(self, provider):
        super(AWSSecurityGroupService, self).__init__(provider)
        self.iface = EC2ServiceFilter(self.provider,
                                      'security_groups', AWSSecurityGroup)

    def get(self, group_id):
        """Returns a security group given its ID"""
        return self.iface.get(group_id, 'group-id')

    def list(self, limit=None, marker=None):
        """List all security groups associated with this account"""
        return self.iface.list(limit=limit, marker=marker)

    def find(self, name, limit=None, marker=None):
        """Searches for a security group by name"""
        return self.iface.find(name, 'group-name', limit=limit, marker=marker)

    def create(self, name, description, network_id=None):
        """Creates a security group pair"""
        res = self.iface.create('create_security_group',
                                GroupName=name,
                                Description=description)
        if not self.iface.wait_for_create(res.id, 'group-id'):
            return None
        return res

    def delete(self, group_id):
        """Deletes a security group by ID"""
        res = self.iface.get(group_id, 'group-id')
        if res:
            self.iface.delete(res.id, 'group-id')
            return self.iface.wait_for_delete(res.id, 'group-id')
        return None


class AWSBlockStoreService(BaseBlockStoreService):

    def __init__(self, provider):
        super(AWSBlockStoreService, self).__init__(provider)

        # Initialize provider services
        self._volume_svc = AWSVolumeService(self.provider)
        self._snapshot_svc = AWSSnapshotService(self.provider)

    @property
    def volumes(self):
        return self._volume_svc

    @property
    def snapshots(self):
        return self._snapshot_svc


class AWSVolumeService(BaseVolumeService):

    def __init__(self, provider):
        super(AWSVolumeService, self).__init__(provider)
        self.iface = EC2ServiceFilter(self.provider,
                                      'volumes', AWSVolume)

    def get(self, volume_id):
        """Returns a volume given its ID"""
        return self.iface.get(volume_id, 'volume-id')

    def list(self, limit=None, marker=None):
        """List all volumes associated with this account"""
        return self.iface.list(limit=limit, marker=marker)

    def find(self, name, limit=None, marker=None):
        """Searches for a volume by name"""
        return self.iface.find(name, 'tag:Name', limit=limit, marker=marker)

    def create(self, name, size, zone,
               snapshot=None, iops=None, description=None):
        """Creates a volume"""
        zone_id = zone.id if isinstance(zone, PlacementZone) else zone
        snapshot_id = snapshot.id if isinstance(
            snapshot, AWSSnapshot) and snapshot else snapshot
        params = {
            'Size': size,
            'AvailabilityZone': zone_id
        }
        if iops:
            params['Iops'] = iops
        if snapshot_id:
            params['SnapshotId'] = snapshot_id
        res = self.iface.create('create_volume', **params)
        if not self.iface.wait_for_create(res.id, 'volume-id'):
            return None
        res.name = name
        if res.description:
            res.description = description
        return res

    def delete(self, name):
        """Deletes a volume by name"""
        res = self.iface.find(name, 'tag:Name')
        if res:
            res = res[0]
            self.iface.delete(res.id, 'volume-id')
            return self.iface.wait_for_delete(res.id, 'volume-id')
        return None

class AWSSnapshotService(BaseSnapshotService):

    def __init__(self, provider):
        super(AWSSnapshotService, self).__init__(provider)
        self.iface = EC2ServiceFilter(self.provider,
                                      'snapshots', AWSSnapshot)

    def get(self, volume_id):
        """Returns a snapshot given its ID"""
        return self.iface.get(volume_id, 'snapshot-id')

    def list(self, limit=None, marker=None):
        """List all snapshots associated with this account"""
        return self.iface.list(limit=limit, marker=marker)

    def find(self, name, limit=None, marker=None):
        """Searches for a snapshot by name"""
        return self.iface.find(name, 'tag:Name', limit=limit, marker=marker)

    def create(self, name, volume, description=None):
        """Creates a snapshot"""
        volume_id = volume.id if isinstance(volume, AWSVolume) else volume
        res = self.iface.create('create_snapshot',
                                VolumeId=volume_id)
        if not self.iface.wait_for_create(res.id, 'snapshot-id'):
            return None
        res.name = name
        if res.description:
            res.description = description
        return res

    def delete(self, name):
        """Deletes a snapshot by name"""
        res = self.iface.find(name, 'tag:Name')
        if res:
            res = res[0]
            self.iface.delete(res.id, 'snapshot-id')
            return self.iface.wait_for_delete(res.id, 'snapshot-id')
        return None


class AWSObjectStoreService(BaseObjectStoreService):

    def __init__(self, provider):
        super(AWSObjectStoreService, self).__init__(provider)

    def get(self, bucket_id):
        """
        Returns a bucket given its ID. Returns ``None`` if the bucket
        does not exist.
        """
        bucket = self.provider.s3_conn.lookup(bucket_id)
        if bucket:
            return AWSBucket(self.provider, bucket)
        else:
            return None

    def find(self, name, limit=None, marker=None):
        """
        Searches for a bucket by a given list of attributes.
        """
        buckets = [AWSBucket(self.provider, bucket)
                   for bucket in self.provider.s3_conn.get_all_buckets()
                   if name in bucket.name]
        return ClientPagedResultList(self.provider, buckets,
                                     limit=limit, marker=marker)

    def list(self, limit=None, marker=None):
        """
        List all containers.
        """
        buckets = [AWSBucket(self.provider, bucket)
                   for bucket in self.provider.s3_conn.get_all_buckets()]
        return ClientPagedResultList(self.provider, buckets,
                                     limit=limit, marker=marker)

    def create(self, name, location=None):
        """
        Create a new bucket.
        """
        bucket = self.provider.s3_conn.create_bucket(
            name,
            location=location if location else '')
        return AWSBucket(self.provider, bucket)


class AWSImageService(BaseImageService):

    def __init__(self, provider):
        super(AWSImageService, self).__init__(provider)
        self.iface = EC2ServiceFilter(self.provider,
                                      'images', AWSMachineImage)

    def get(self, image_id):
        """Returns a image given its ID"""
        return self.iface.get(image_id, 'image-id')

    def list(self, limit=None, marker=None):
        """List all images associated with this account"""
        return self.iface.list(limit=limit, marker=marker)

    def find(self, name, limit=None, marker=None):
        """Searches for a image by name"""
        return self.iface.find(name, 'name', limit=limit, marker=marker)


class AWSComputeService(BaseComputeService):

    def __init__(self, provider):
        super(AWSComputeService, self).__init__(provider)
        self._instance_type_svc = AWSInstanceTypesService(self.provider)
        self._instance_svc = AWSInstanceService(self.provider)
        self._region_svc = AWSRegionService(self.provider)
        self._images_svc = AWSImageService(self.provider)

    @property
    def images(self):
        return self._images_svc

    @property
    def instance_types(self):
        return self._instance_type_svc

    @property
    def instances(self):
        return self._instance_svc

    @property
    def regions(self):
        return self._region_svc


class AWSInstanceService(BaseInstanceService):

    def __init__(self, provider):
        super(AWSInstanceService, self).__init__(provider)

    def create(self, name, image, instance_type, zone=None,
               key_pair=None, security_groups=None, user_data=None,
               launch_config=None,
               **kwargs):
        """
        Creates a new virtual machine instance.

        If no VPC/subnet was specified (via ``launch_config`` parameter), this
        method will search for a default VPC and attempt to launch an instance
        into that VPC.
        """
        image_id = image.id if isinstance(image, MachineImage) else image
        instance_size = instance_type.id if \
            isinstance(instance_type, InstanceType) else instance_type
        zone_id = zone.id if isinstance(zone, PlacementZone) else zone
        key_pair_name = key_pair.name if isinstance(
            key_pair,
            KeyPair) else key_pair
        if launch_config:
            bdm = self._process_block_device_mappings(launch_config, zone_id)
            subnet_id = self._get_net_id(launch_config)
        else:
            bdm = subnet_id = None
        subnet_id, zone_id, security_group_ids = \
            self._resolve_launch_options(subnet_id, zone_id, security_groups)

        reservation = self.provider.ec2_conn.run_instances(
            image_id=image_id, instance_type=instance_size,
            min_count=1, max_count=1, placement=zone_id,
            key_name=key_pair_name, security_group_ids=security_group_ids,
            user_data=user_data, block_device_map=bdm, subnet_id=subnet_id)
        instance = None
        if reservation:
            time.sleep(2)  # The instance does not always get created in time
            instance = AWSInstance(self.provider, reservation.instances[0])
            instance.name = name
        return instance

    def _resolve_launch_options(self, subnet_id, zone_id, security_groups):
        """
        Resolve inter-dependent launch options.

        With launching into VPC only, try to figure out a default
        VPC to launch into, making placement decisions along the way that
        are implied from the zone a subnet exists in.
        """
        def _deduce_subnet_and_zone(vpc, zone_id=None):
            """
            Figure out subnet ID from a VPC (and zone_id, if not supplied).
            """
            if zone_id:
                # A placement zone was specified. Choose the default
                # subnet in that zone.
                for sn in vpc.subnets():
                    if sn._subnet.availability_zone == zone_id:
                        subnet_id = sn.id
            else:
                # No zone was requested, so just pick one subnet
                sn = vpc.subnets()[0]
                subnet_id = sn.id
                zone_id = sn._subnet.availability_zone
            return subnet_id, zone_id

        vpc_id = None
        sg_ids = []
        if subnet_id:
            # Subnet was supplied - get the VPC so named SGs can be resolved
            subnet = self.provider.vpc_conn.get_all_subnets(subnet_id)[0]
            vpc_id = subnet.vpc_id
            # zone_id must match zone where the requested subnet lives
            if zone_id and subnet.availability_zone != zone_id:
                raise ValueError("Requested placement zone ({0}) must match "
                                 "specified subnet's availability zone ({1})."
                                 .format(zone_id, subnet.availability_zone))
        if security_groups:
            # Try to get a subnet via specified SGs. This will work only if
            # the specified SGs are within a VPC (which is a prerequisite to
            # launch into VPC anyhow).
            _sg_ids = self._process_security_groups(security_groups, vpc_id)
            # Must iterate through all the SGs here because a SG name may
            # exist in a VPC or EC2-Classic so opt for the VPC SG. This
            # applies in the case no subnet was specified.
            if not subnet_id:
                for sg_id in _sg_ids:
                    sg = self.provider.security.security_groups.get(sg_id)
                    if sg._security_group.vpc_id:
                        if sg_ids and sg_id not in sg_ids:
                            raise ValueError("Multiple matches for VPC "
                                             "security group(s) {0}."
                                             .format(security_groups))
                        else:
                            sg_ids.append(sg_id)
                        vpc = self.provider.network.get(
                            sg._security_group.vpc_id)
                        subnet_id, zone_id = _deduce_subnet_and_zone(
                            vpc, zone_id)
            else:
                sg_ids = _sg_ids
            if not subnet_id:
                raise AttributeError("Supplied security group(s) ({0}) must "
                                     "be associated with a VPC."
                                     .format(security_groups))
        if not subnet_id and not security_groups:
            # No VPC/subnet was supplied, search for the default VPC.
            for vpc in self.provider.network.list():
                if vpc._vpc.is_default:
                    vpc_id = vpc.id
                    subnet_id, zone_id = _deduce_subnet_and_zone(vpc, zone_id)
            if not vpc_id:
                raise AttributeError("No default VPC exists. Supply a "
                                     "subnet to launch into (via "
                                     "launch_config param).")
        return subnet_id, zone_id, sg_ids

    def _process_security_groups(self, security_groups, vpc_id=None):
        """
        Process security groups to create a list of SG ID's for launching.

        :type security_groups: A ``list`` of ``SecurityGroup`` objects or a
                               list of ``str`` names
        :param security_groups: A list of ``SecurityGroup`` objects or a list
                                of ``SecurityGroup`` names, which should be
                                assigned to this instance.

        :type vpc_id: ``str``
        :param vpc_id: A VPC ID within which the supplied security groups exist

        :rtype: ``list``
        :return: A list of security group IDs.
        """
        if isinstance(security_groups, list) and \
                isinstance(security_groups[0], SecurityGroup):
            sg_ids = [sg.id for sg in security_groups]
        else:
            # SG names were supplied, need to map them to SG IDs.
            sg_ids = []
            # If a VPC was specified, need to map to the SGs in the VPC.
            flters = None
            if vpc_id:
                flters = {'vpc_id': vpc_id}
            sgs = self.provider.ec2_conn.get_all_security_groups(
                filters=flters)
            sg_ids = [sg.id for sg in sgs if sg.name in security_groups]

        return sg_ids

    def _process_block_device_mappings(self, launch_config, zone=None):
        """
        Processes block device mapping information
        and returns a Boto BlockDeviceMapping object. If new volumes
        are requested (source is None and destination is VOLUME), they will be
        created and the relevant volume ids included in the mapping.
        """
        bdm = BlockDeviceMapping()
        # Assign letters from f onwards
        # http://docs.aws.amazon.com/AWSEC2/latest/UserGuide/device_naming.html
        next_letter = iter(list(string.ascii_lowercase[6:]))
        # assign ephemeral devices from 0 onwards
        ephemeral_counter = 0
        for device in launch_config.block_devices:
            bd_type = BlockDeviceType()

            if device.is_volume:
                if device.is_root:
                    bdm['/dev/sda1'] = bd_type
                else:
                    bdm['sd' + next(next_letter)] = bd_type

                if isinstance(device.source, Snapshot):
                    bd_type.snapshot_id = device.source.id
                elif isinstance(device.source, Volume):
                    bd_type.volume_id = device.source.id
                elif isinstance(device.source, MachineImage):
                    # Not supported
                    pass
                else:
                    # source is None, but destination is volume, therefore
                    # create a blank volume. If the Zone is None, this
                    # could fail since the volume and instance may be created
                    # in two different zones.
                    if not zone:
                        raise InvalidConfigurationException(
                            "A zone must be specified when launching with a"
                            " new blank volume block device mapping.")
                    new_vol = self.provider.block_store.volumes.create(
                        '',
                        device.size,
                        zone)
                    bd_type.volume_id = new_vol.id
                bd_type.delete_on_terminate = device.delete_on_terminate
                if device.size:
                    bd_type.size = device.size
            else:  # device is ephemeral
                bd_type.ephemeral_name = 'ephemeral%s' % ephemeral_counter

        return bdm

    def _get_net_id(self, launch_config):
        return (launch_config.network_interfaces[0]
                if len(launch_config.network_interfaces) > 0
                else None)

    def create_launch_config(self):
        return AWSLaunchConfig(self.provider)

    def get(self, instance_id):
        """
        Returns an instance given its id. Returns None
        if the object does not exist.
        """
        reservation = self.provider.ec2_conn.get_all_reservations(
            instance_ids=[instance_id])
        if reservation:
            return AWSInstance(self.provider, reservation[0].instances[0])
        else:
            return None

    def find(self, name, limit=None, marker=None):
        """
        Searches for an instance by a given list of attributes.

        :rtype: ``object`` of :class:`.Instance`
        :return: an Instance object
        """
        filtr = {'tag:Name': name}
        reservations = self.provider.ec2_conn.get_all_reservations(
            filters=filtr,
            max_results=limit,
            next_token=marker)
        instances = [AWSInstance(self.provider, inst)
                     for res in reservations
                     for inst in res.instances]
        return ServerPagedResultList(reservations.is_truncated,
                                     reservations.next_token,
                                     False, data=instances)

    def list(self, limit=None, marker=None):
        """
        List all instances.
        """
        reservations = self.provider.ec2_conn.get_all_reservations(
            max_results=limit,
            next_token=marker)
        instances = [AWSInstance(self.provider, inst)
                     for res in reservations
                     for inst in res.instances]
        return ServerPagedResultList(reservations.is_truncated,
                                     reservations.next_token,
                                     False, data=instances)

AWS_INSTANCE_DATA_DEFAULT_URL = "https://d168wakzal7fp0.cloudfront.net/" \
                                "aws_instance_data.json"


class AWSInstanceTypesService(BaseInstanceTypesService):

    def __init__(self, provider):
        super(AWSInstanceTypesService, self).__init__(provider)

    @property
    def instance_data(self):
        """
        TODO: Needs a caching function with timeout
        """
        r = requests.get(self.provider.config.get(
            "aws_instance_info_url", AWS_INSTANCE_DATA_DEFAULT_URL))
        return r.json()

    def list(self, limit=None, marker=None):
        inst_types = [AWSInstanceType(self.provider, inst_type)
                      for inst_type in self.instance_data]
        return ClientPagedResultList(self.provider, inst_types,
                                     limit=limit, marker=marker)


class AWSRegionService(BaseRegionService):

    def __init__(self, provider):
        super(AWSRegionService, self).__init__(provider)

    def get(self, region_id):
        region = self.provider.ec2_conn.meta.client.describe_regions(
            Filters=[{
                'Name': 'region-name',
                'Values': [region_id]
            }]
        ).get('Regions', list())
        return AWSRegion(self.provider, region[0]) if len(region) else None

    def list(self, limit=None, marker=None):
        regions = [
            AWSRegion(self.provider, x) for x in
            self.provider.ec2_conn.meta.client.describe_regions()
            .get('Regions', list())
        ]
        return ClientPagedResultList(self.provider, regions,
                                     limit=limit, marker=marker)

    @property
    def current(self):
        return self.get(self.provider.session.region_name)


class AWSNetworkService(BaseNetworkService):

    def __init__(self, provider):
        super(AWSNetworkService, self).__init__(provider)
        self._subnet_svc = AWSSubnetService(self.provider)
        self.iface = EC2ServiceFilter(self.provider,
                                      'vpcs', AWSNetwork)
        self.iface_vips = EC2ServiceFilter(self.provider,
                                           'vpc_addresses', AWSFloatingIP)
        self.iface_igws = EC2ServiceFilter(self.provider,
                                           'internet_gateways', AWSRouter)

    def get(self, network_id):
        """Returns a network given its ID"""
        return self.iface.get(network_id, 'vpc-id')

    def list(self, limit=None, marker=None):
        """List all networks associated with this account"""
        return self.iface.list(limit=limit, marker=marker)

    def find(self, name, limit=None, marker=None):
        """Searches for a network by name"""
        return self.iface.find(name, 'tag:Name', limit=limit, marker=marker)

    def floating_ips(self, network_id=None):
        return [
            x for x in self.iface_vips.list()
            if not network_id or x.network_interface_id == network_id
        ]

    def routers(self):
        return self.iface_igws.list()

    def create(self, name=None):
        # AWS requried CIDR block to be specified when creating a network
        # so set a default one and use the largest possible netmask.
        default_cidr = '10.0.0.0/16'
        res = self.iface.create('create_vpc', CidrBlock=default_cidr)
        if not wait_for_load(res):
            return None
        if name:
            res.name = name
        return res

    @property
    def subnets(self):
        return self._subnet_svc

    def create_floating_ip(self):
        return AWSFloatingIP(
            self.provider,
            self.provider.ec2_conn.VpcAddress(
                self.provider.ec2_conn.meta.client.allocate_address(
                    Domain='vpc')['AllocationId']))

    def create_router(self, name=None):
        timeout = 10
        res = self.iface.create('create_internet_gateway')
        if not wait_for_load(res):
            return None
        if name:
            res.name = name
        return res


class AWSSubnetService(BaseSubnetService):

    def __init__(self, provider):
        super(AWSSubnetService, self).__init__(provider)
        self.iface = EC2ServiceFilter(self.provider,
                                      'subnets', AWSSubnet)

    def get(self, subnet_id):
        """Returns a subnet given its ID"""
        return self.iface.get(subnet_id, 'subnet-id')

    def list(self, network=None, limit=None, marker=None):
        """List all subnets associated with this account"""
        network_id = network.id if isinstance(network, AWSNetwork) else network
        return [
            x for x in self.iface.list(limit=limit, marker=marker)
            if not network_id or x.network_id == network_id
        ]

    def find(self, name, limit=None, marker=None):
        """Searches for a subnet by name"""
        return self.iface.find(name, 'tag:Name', limit=limit, marker=marker)

    def create(self, network, cidr_block, name=None):
        network_id = network.id if isinstance(network, AWSNetwork) else network
        res = self.iface.create('create_subnet',
                                VpcId=network_id,
                                CidrBlock=cidr_block)
        if name:
            res.name = name
        return res

    def delete(self, subnet):
        subnet_id = subnet.id if isinstance(subnet, AWSSubnet) else subnet
        return self.iface.delete(subnet_id, 'subnet-id')
