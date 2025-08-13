from aprsd.packets import core


def _get_tab_id(tabname: str) -> str:
    """Get the tab id for a tabname."""
    return f"t_{tabname}"


def _get_packet_id(packet: type[core.Packet], timestamp: bool = False) -> str:
    """Get the packet id for a packet."""
    if not packet.msgNo:
        return f"_{hash(packet)}"
    else:
        return f"_{packet.msgNo}"
