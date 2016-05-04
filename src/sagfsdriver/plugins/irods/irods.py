#!/usr/bin/env python

"""
   Copyright 2014 The Trustees of Princeton University

   Licensed under the Apache License, Version 2.0 (the "License");
   you may not use this file except in compliance with the License.
   You may obtain a copy of the License at

       http://www.apache.org/licenses/LICENSE-2.0

   Unless required by applicable law or agreed to in writing, software
   distributed under the License is distributed on an "AS IS" BASIS,
   WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
   See the License for the specific language governing permissions and
   limitations under the License.
"""

"""
General iRODS Plugin
"""
import os
import time
import logging
import threading
import sagfsdriver.lib.abstractfs as abstractfs
import sagfsdriver.plugins.datastore.irods_client as irods_client

logger = logging.getLogger('syndicate_iRODS_filesystem')
logger.setLevel(logging.DEBUG)
# create file handler which logs even debug messages
fh = logging.FileHandler('syndicate_iRODS_filesystem.log')
fh.setLevel(logging.DEBUG)
# create formatter and add it to the handlers
formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
fh.setFormatter(formatter)
# add the handlers to the logger
logger.addHandler(fh)

class plugin_impl(abstractfs.afsbase):
    def __init__(self, config, role=abstractfs.afsrole.DISCOVER):
        if not config:
            raise ValueError("fs configuration is not given correctly")

        dataset_root = config.get("dataset_root")
        if not dataset_root:
            raise ValueError("dataset_root configuration is not given correctly")

        secrets = config.get("secrets")
        if not secrets:
            raise ValueError("secrets are not given correctly")

        user = secrets.get("user")
        user = user.encode('ascii','ignore')
        if not user:
            raise ValueError("user is not given correctly")

        password = secrets.get("password")
        password = password.encode('ascii','ignore')
        if not password:
            raise ValueError("password is not given correctly")

        irods_config = config.get("irods")
        if not irods_config:
            raise ValueError("irods configuration is not given correctly")

        # set role
        self.role = role

        # config can have unicode strings
        dataset_root = dataset_root.encode('ascii','ignore')
        self.dataset_root = dataset_root.rstrip("/")

        self.irods_config = irods_config

        # init irods client
        # we convert unicode (maybe) strings to ascii since python-irodsclient cannot accept unicode strings
        irods_host = self.irods_config["host"]
        irods_host = irods_host.encode('ascii','ignore')
        irods_zone = self.irods_config["zone"]
        irods_zone = irods_zone.encode('ascii','ignore')
        
        logger.info("__init__: initializing irods_client")
        self.irods = irods_client.irods_client(host=irods_host, 
                                               port=self.irods_config["port"], 
                                               user=user, 
                                               password=password, 
                                               zone=irods_zone)


        self.notification_cb = None
        # create a re-entrant lock (not a read lock)
        self.lock = threading.RLock()

    def _lock(self):
        self.lock.acquire()

    def _unlock(self):
        self.lock.release()

    def on_update_detected(self, operation, path):
        ascii_path = path.encode('ascii','ignore')
        driver_path = self._make_driver_path(ascii_path)

        self.clear_cache(driver_path)
        if operation == "remove":
            if self.notification_cb:
                entry = {}
                entry["path"] = driver_path
                entry["stat"] = None
                self.notification_cb([], [], [entry])
        elif operation in ["create", "modify"]:
            if self.notification_cb:
                st = self.stat(driver_path)
                entry = {}
                entry["path"] = driver_path
                entry["stat"] = st
                if operation == "create":
                    self.notification_cb([], [entry], [])
                elif operation == "modify":
                    self.notification_cb([entry], [], [])

    def _make_irods_path(self, path):
        if path.startswith(self.dataset_root):
            return path
        
        if path.startswith("/"):
            return self.dataset_root + path

        return self.dataset_root + "/" + path

    def _make_driver_path(self, path):
        if path.startswith(self.dataset_root):
            return path[len(self.dataset_root):]
        return path

    def connect(self):
        logger.info("connect: connecting to iRODS")
        self.irods.connect()

        if self.role == abstractfs.afsrole.DISCOVER:
            if not self.irods.exists(self.dataset_root):
                raise IOError("dataset root does not exist")

    def close(self):
        logger.info("close")
        logger.info("close: closing iRODS")
        if self.irods:
            self.irods.close()

    def stat(self, path):
        self._lock()
        ascii_path = path.encode('ascii','ignore')
        irods_path = self._make_irods_path(ascii_path)
        driver_path = self._make_driver_path(ascii_path)
        # get stat
        sb = self.irods.stat(irods_path)
        self._unlock()
        return abstractfs.afsstat(directory=sb.directory, 
                                  path=driver_path,
                                  name=os.path.basename(driver_path), 
                                  size=sb.size,
                                  checksum=sb.checksum,
                                  create_time=sb.create_time,
                                  modify_time=sb.modify_time)

    def exists(self, path):
        self._lock()
        ascii_path = path.encode('ascii','ignore')
        irods_path = self._make_irods_path(ascii_path)
        exist = self.irods.exists(irods_path)
        self._unlock()
        return exist

    def list_dir(self, dirpath):
        self._lock()
        ascii_path = dirpath.encode('ascii','ignore')
        irods_path = self._make_irods_path(ascii_path)
        l = self.irods.list_dir(irods_path)
        self._unlock()
        return l

    def is_dir(self, dirpath):
        self._lock()
        ascii_path = dirpath.encode('ascii','ignore')
        irods_path = self._make_irods_path(ascii_path)
        d = self.irods.is_dir(irods_path)
        self._unlock()
        return d

    def read(self, filepath, offset, size):
        self._lock()
        ascii_path = filepath.encode('ascii','ignore')
        irods_path = self._make_irods_path(ascii_path)
        buf = None
        try:
            buf = self.irods.read(irods_path, offset, size)
        except Exception, e:
            logger.error("Failed to read %s: %s" % (irods_path, e))

        self._unlock()
        return buf

    def clear_cache(self, path):
        self._lock()
        if path:
            ascii_path = path.encode('ascii','ignore')
            irods_path = self._make_irods_path(ascii_path)
            self.irods.clear_stat_cache(irods_path)
        else:
            self.irods.clear_stat_cache(None)
        self._unlock()

    def plugin(self):
        return self.__class__

    def role(self):
        return self.role

    def set_notification_cb(self, notification_cb):
        self.notification_cb = notification_cb


