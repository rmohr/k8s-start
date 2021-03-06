#!/usr/bin/python3
# -*- coding: utf-8 -*-
# vim: sw=4 et sts=4
#
# fuzz
#
# Copyright (C) 2016  Red Hat, Inc.
#
# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation; either version 2 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program.  If not, see <http://www.gnu.org/licenses/>.
#
# Author(s): Fabian Deutsch <fabiand@redhat.com>


import subprocess
import json
import os
import concurrent.futures
from .utils import jsonpath


THREADS = 2


def kubectl(args, expr=None, **kwargs):
    app = os.environ.get("KUBECTL", "kubectl")
    argv = [app] + args
    print(argv)
    data = subprocess.check_output(argv, **kwargs)
    data = data.decode("utf-8")
    print(data)
    if expr:
        objs = json.loads(data)
        return jsonpath(expr, objs)
    return data


class KubeDomainRuntime():
    VM_RC_SPEC = """
apiVersion: v1
kind: ReplicationController
metadata:
  name: compute-rc-{DOMNAME}
  labels:
    app: compute-rc
    domain: {DOMNAME}
spec:
  replicas: 1
  template:
    metadata:
      labels:
        app: compute
        domain: {DOMNAME}
    spec:
      hostNetwork: True
      containers:
      - name: compute
        image: docker.io/fabiand/compute:latest
        securityContext:
          privileged: true
        ports:
        - containerPort: 1923
          name: spice
        - containerPort: 5900
          name: vnc
        - containerPort: 16509
          name: libvirt
        env:
        - name: LIBVIRT_DOMAIN
          value: {DOMNAME}
        - name: DOMAIN_HTTP_URL
          value: {DOMAIN_HTTP_URL}
        resources:
          requests:
            memory: "128Mi"
            cpu: "1000m"
          limits:
            memory: "1024Mi"
            cpu: "4000m"
    """

    VM_SVC_SPEC = """
apiVersion: v1
kind: Service
metadata:
  name: libvirt-{DOMNAME}
  labels:
    app: compute-service
    domain: {DOMNAME}
spec:
  selector:
    app: compute
    domain: {DOMNAME}
  ports:
  - name: libvirt
    port: 16509
  - name: vnc
    port: 5900
    """

    def list(self):
        matches = kubectl(["-l", "app=compute-rc",
                          "get", "rc", "-ojson"],
                          "items[*].metadata.labels.domain")
        return [m.value for m in matches] if matches else []

    def create(self, domname):
        def create(spec):
            env = {"DOMNAME": domname,
                   "DOMAIN_HTTP_URL": "%s/%s" % (os.environ["POD_IP"],
                                                 domname)
                   }

            spec = spec.format(**env)
            print(spec)
            kubectl(["create", "-f", "-"], input=bytes(spec, encoding="utf8"))

        with concurrent.futures.ThreadPoolExecutor(THREADS) as executor:
            executor.submit(create, self.VM_RC_SPEC)
            executor.submit(create, self.VM_SVC_SPEC)

    def delete(self, domname):
        with concurrent.futures.ThreadPoolExecutor(THREADS) as executor:
            executor.submit(kubectl, ["delete", "rc",
                                      "-l", "domain=%s" % domname])

            executor.submit(kubectl, ["delete", "svc",
                                      "-l", "domain=%s" % domname])

    def connection_uri(self, domname):
        data = kubectl(["get", "service", "-ojson",
                        "-l", "domain=%s" % domname])
        # FIXME directly fetch for instance
        parsed = json.loads(data)
        clusterip = jsonpath("items[0].spec.clusterIP",
                             parsed)[0].value
        port = jsonpath("items[0].spec.ports[?(name='libvirt')].port",
                        parsed)[0].value
        return "qemu+tcp://%s:%s/system" % (clusterip, port)


class FakeRuntime():
    running = set()

    def list(self):
        return list(self.running)

    def create(self, domname):
        self.running.add(domname)

    def delete(self, domname):
        self.running.remove(domname)

    def connection_uri(self, domname):
        return "none://%s/system" % domname
