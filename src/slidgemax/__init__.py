# Copyright 2026 @black-roland
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Slidge-based XMPP gateway for the MAX messenger."""

from __future__ import annotations

__version__ = "0.4.0"

from .gateway import Gateway
from .session import Session

__all__ = ["Gateway", "Session", "__version__", "main"]


def main() -> None:
    from slidge import entrypoint

    entrypoint("slidgemax")
