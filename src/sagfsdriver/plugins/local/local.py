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
Local-filesystem Plugin
"""
import os
import sys
import time
import stat
import logging
import threading
import pyinotify

import sagfsdriver.lib.abstractfs as abstractfs

logger = logging.getLogger('syndicate_local_filesystem')
logger.setLevel(logging.DEBUG)
# create file handler which logs even debug messages
fh = logging.FileHandler('syndicate_local_filesystem.log')
fh.setLevel(logging.DEBUG)
# create formatter and add it to the handlers
formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
fh.setFormatter(formatter)
# add the handlers to the logger
logger.addHandler(fh)

class InotifyEventHandler(pyinotify.ProcessEvent):
    def __init__(self, plugin):
        self.plugin = plugin

    def process_IN_CREATE(self, event):
        logger.info("Creating: %s" % event.pathname)
        self.plugin.on_update_detected("create", event.pathname)

    def process_IN_DELETE(self, event):
        logger.info("Removing: %s" % event.pathname)
        self.plugin.on_update_detected("remove", event.pathname)

    def process_IN_MODIFY(self, event):
        logger.info("Modifying: %s" % event.pathname)
        self.plugin.on_update_detected("modify", event.pathname)

    def process_IN_ATTRIB(self, event):
        logger.info("Modifying attributes: %s" % event.pathname)
        self.plugin.on_update_detected("modify", event.pathname)

    def process_IN_MOVED_FROM(self, event):
        logger.info("Moving a file from : %s" % event.pathname)
        self.plugin.on_update_detected("remove", event.pathname)

    def process_IN_MOVED_TO(self, event):
        logger.info("Moving a file to : %s" % event.pathname)
        self.plugin.on_update_detected("create", event.pathname)

    def process_default(self, event):
        logger.info("Unhandled event to a file : %s" % event.pathname)
        logger.info("- %s" % event)

class plugin_impl(abstractfs.afsbase):
    def __init__(self, config, role=abstractfs.afsrole.DISCOVER):
        if not config:
            raise ValueError("fs configuration is not given correctly")

        dataset_root = config.get("dataset_root")
        if not dataset_root:
            raise ValueError("dataset_root configuration is not given correctly")

        # set role
        self.role = role

        # config can have unicode strings
        dataset_root = dataset_root.encode('ascii','ignore')
        self.dataset_root = dataset_root.rstrip("/")

        if self.role == abstractfs.afsrole.DISCOVER:
            # set inotify
            self.watch_manager = pyinotify.WatchManager()
            self.notify_handler = InotifyEventHandler(self)
            self.notifier = pyinotify.ThreadedNotifier(self.watch_manager, 
                                                       self.notify_handler)

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

    def _make_localfs_path(self, path):
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
        if self.role == abstractfs.afsrole.DISCOVER:
            if not os.path.exists(self.dataset_root):
                raise IOError("dataset root does not exist")

            try:
                # start monitoring
                self.notifier.start()

                mask = pyinotify.IN_DELETE | pyinotify.IN_CREATE | pyinotify.IN_MODIFY | pyinotify.IN_ATTRIB | pyinotify.IN_MOVED_FROM | pyinotify.IN_MOVED_TO | pyinotify.IN_MOVE_SELF
                self.watch_directory = self.watch_manager.add_watch(self.dataset_root, 
                                                                    mask, 
                                                                    rec=True,
                                                                    auto_add=True)
            except:
                self.close()

    def close(self):
        if self.role == abstractfs.afsrole.DISCOVER:
            if self.watch_manager and self.watch_directory:
                self.watch_manager.rm_watch(self.watch_directory.values())

            if self.notifier:
                self.notifier.stop()

    def stat(self, path):
        self._lock()
        ascii_path = path.encode('ascii','ignore')
        localfs_path = self._make_localfs_path(ascii_path)
        driver_path = self._make_driver_path(ascii_path)
        # get stat
        sb = os.stat(localfs_path)
        self._unlock()
        return abstractfs.afsstat(directory=stat.S_ISDIR(sb.st_mode), 
                                  path=driver_path,
                                  name=os.path.basename(driver_path), 
                                  size=sb.st_size,
                                  checksum=0,
                                  create_time=sb.st_ctime,
                                  modify_time=sb.st_mtime)

    def exists(self, path):
        self._lock()
        ascii_path = path.encode('ascii','ignore')
        localfs_path = self._make_localfs_path(ascii_path)
        exist = os.path.exists(localfs_path)
        self._unlock()
        return exist

    def list_dir(self, dirpath):
        self._lock()
        ascii_path = dirpath.encode('ascii','ignore')
        localfs_path = self._make_localfs_path(ascii_path)
        l = os.listdir(localfs_path)
        self._unlock()
        return l

    def is_dir(self, dirpath):
        self._lock()
        ascii_path = dirpath.encode('ascii','ignore')
        localfs_path = self._make_localfs_path(ascii_path)
        d = False
        if os.path.exists(localfs_path):
            sb = os.stat(localfs_path)
            d = stat.S_ISDIR(sb.st_mode)
        self._unlock()
        return d

    def read(self, filepath, offset, size):
        self._lock()
        ascii_path = filepath.encode('ascii','ignore')
        localfs_path = self._make_localfs_path(ascii_path)
        buf = None
        try:
            with open(localfs_path, "r") as f:
                f.seek(offset)
                buf = f.read(size)
        except Exception, e:
            logger.error("Failed to read %s: %s" % (localfs_path, e))

        self._unlock()
        return buf

    def clear_cache(self, path):
        pass

    def plugin(self):
        return self.__class__

    def role(self):
        return self.role

    def set_notification_cb(self, notification_cb):
        self.notification_cb = notification_cb


