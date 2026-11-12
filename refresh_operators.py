from autostory.common import DATA, DeviceLock, log
from autostory.device import Device
from autostory.core import Core

if __name__=='__main__':
    with DeviceLock():
        core=Core(Device())
        try:core.refresh_box()
        finally:core.close()
