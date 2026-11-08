#!/usr/bin/env python3
"""Compatibility entrypoint for existing deployments; implementation ships in the wheel."""
from brodar.service_supervisor import main
if __name__=='__main__':main()
