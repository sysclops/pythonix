# TODO: Check dependencies #


def __set_idle_name(name, n):

    p_z = False

    if n > 999:
        n = 999

    name = 'idle'

    i = 4
    c = 100
    while c > 0:
        digit = n // c
        n -= digit * c
        if p_z or digit != 0 or c == 1:
            p_z = True
            name = ''.join([name, chr(ord('0')+digit)])
            i += 1
        c = c // 10

    return name

PICK_ANY = 1
PICK_HIGHERONLY = 2


def BuildNotifyMessage(m_ptr, src, dst_ptr):
    m_ptr['m_type'] = NOTIFY_MESSAGE
    m_ptr['NOTIFY_TIMESTAMP'] = get_monotonic()
    # TODO: Check priv function
    if src == HARDWARE:
        m_ptr['NOTIFY_TAG'] = dst_ptr['s_int_pending']
        dst_ptr['s_int_pending'] = 0
    elif src == SYSTEM:
        m_ptr['NOTIFY_TAG'] = dst_ptr['s_sig_pending']
        dst_ptr['s_sig_pending'] = 0


def proc_init():

    rp = BEG_PROC_ADDR + 1
    i = -NR_TASKS + 1
    while rp < END_PROC_ADDR:
        rp['p_rts_flags'] = RTS_SLOT_FREE
        rp['p_magic'] = PMAGIC
        rp['p_nr'] = i
        rp['p_endpoint'] = _ENDPOINT(0, rp['p_nr'])
        rp['p_scheduler'] = None
        rp['p_priority'] = 0
        rp['p_quantum_size_ms'] = 0
        arch_proc_reset(rp)
        rp += 1
        i += 1

    sp = BEG_PRIV_ADDR+1
    i = 1
    while sp < END_PRIV_ADDR:
        # TODO: Check Minix NONE value.
        sp['s_proc_nr'] = NONE
        # TODO: Check if this casting is needed #
        sp['s_id'] = sys_id_t(i)
        ppriv_addr[i] = sp
        sp['s_sig_mrg'] = NONE
        sp['s_bak_sig_mgr'] = NONE
        sp += 1
        i += 1

    idle_priv.s_flags = IDL_F

    # Initialize IDLE dicts for every CPU #
    for i in range(CONFIG_MAX_CPUS):
        ip = get_cpu_var_ptr(i, idle_proc)
        ip['p_endpoint'] = IDLE
        ip['p_priv'] = idle_priv
        # Idle must never be scheduled #
        ip['p_rts_flags'] |= RTS_PROC_STOP
        __set_idle_name(ip['p_name'], i)


def __switch_address_space_idle():
    # MXCM #
    ''' Currently we bet that VM is always alive and its pages available so
    when the CPU wakes up the kernel is mapped and no surprises happen.
    This is only a problem if more than 1 cpus are available.'''

    if CONFIG_SMP:
        switch_address_space(proc_addr(VM_PROC_NR))


def __idle():
    # MXCM #
    ''' This function is called whenever there is no work to do.
    Halt the CPU, and measure how many timestamp counter ticks are
    spent not doing anything. This allows test setups to measure
    the CPU utilization of certain workloads with high precision.'''

    get_cpulocal_var(proc_ptr) = get_cpulocal_var_ptr(idle_proc)
    p = get_cpulocal_var_ptr(idle_proc)

    if priv(p)['s_flags'] & BILLABLE:
        get_cpulocal_var(bill_ptr) = p

    __switch_address_space_idle()

    if not CONFIG_SMP:
        restart_local_timer()
    else:
        get_cpulocal_var(cpu_is_idle) = 1
        if (cpuid != bsp_cpu_id):
            stop_local_timer()
        else:
            restart_local_timer()

    # Start accounting for the idle time #
    context_stop(proc_addr(KERNEL))
    if not SPROFILE:
        halt_cpu()
    else:
        if not sprofiling:
            halt_cpu()
        else:
            v = get_cpulocal_var_ptr(idle_interrupted)
            interrupts_enable()
            while not v:
                arch_pause()
            interrupts_disable()
            v = 0
    ''' End of accounting for the idle task does not happen here, the kernel
    is handling stuff for quite a while before it gets back here!'''


# TODO: Translate switch_to_user() #
def switch_to_user():
    pass


# Handler for all synchronous IPC calls #
def __do_sync_ipc(caller_ptr, call_nr, src_dst_e, m_ptr):
    # MXCM #
    '''Check destination. RECEIVE is the only call that accepts ANY (in
    addition to a real endpoint). The other calls (SEND, SENDREC, and NOTIFY)
    require an endpoint to corresponds to a process. In addition, it is
    necessary to check whether a process is allowed to send to a given
    destination.'''

    if (
        call_nr < 0 or
        call_nr > IPCNO_HIGHEST or
        call_nr >= 32 or
        callname != ipc_call_names[call_nr]
    ):
        if DEBUG_ENABLE_IPC_WARNINGS:
            print('sys_call: trap {} not_allowed, caller {}, src_dst {}' \
                  .format(call_nr, proc_nr(caller_ptr), src_dst_e))
        return ETRAPDENIED

    if src_dst_e == ANY:
        if call_nr != RECEIVE:
            return EINVAL
        src_dst_p = int(src_dst_e)
    else:
        if not isokendpt(src_dst_e, src_dst_p):
            return EDEADSRCDST

        # MXCM #
        ''' If the call is to send to a process, i.e., for SEND, SENDNB,
        SENDREC or NOTIFY, verify that the caller is allowed to send to
        the given destination.'''
        if call_nr != RECEIVE:
            if not may_send_to(caller_ptr, src_dst_p):
                if DEBUG_ENABLE_IPC_WARNINGS:
                    print('sys_call: ipc mask denied {} from {} to {}'\
                          .format(callname, caller_ptr['p_endpoint'],
                          src_dst_e))                          
                return ECALLDENIED

    # MXCM #
    ''' Check if the process has privileges for the requested call.
    Calls to the kernel may only be SENDREC, because tasks always
    reply and may not block if the caller doesn't do receive().'''

    if not priv(caller_ptr)['s_trap_mask'] & (1 << call_nr):
        if DEBUG_ENABLE_IPC_WARNINGS:
            print('sys_call: ipc mask denied {} from {} to {}'\
                  .format(callname, caller_ptr['p_endpoint'], src_dst_e))
        return ETRAPDENIED

    if call_nr != SENDREC and call_nr != RECEIVE and iskerneln(src_dst_p):
        if DEBUG_ENABLE_IPC_WARNINGS:
            print('sys_call: ipc mask denied {} from {} to {}'\
                  .format(callname, caller_ptr['p_endpoint'], src_dst_e))
        return ETRAPDENIED

    if call_nr == SENDREC:
        caller_ptr['p_misc_flags'] |= MF_REPLY_PEND
        # TODO tweak logic to swcase fall
    elif call_nr == SEND:
        result = mini_send(caller_ptr, src_dst_e, m_ptr, 0)
        if call_nr == SEND or result != OK:
            pass       
        # TODO tweak logic to swcase break
        # TODO tweak logic to swcase fall
    elif call_nr == RECEIVE:
        # TODO tweak logic to swcase recheck
        caller_ptr['p_misc_flags'] &= ~MF_REPLY_PEND
        IPC_STATUS_CLEAR(caller_ptr)
        result = mini_receive(caller_ptr, src_dst_e, m_ptr, 0)
    elif call_nr == NOTIFY:
        result = mini_notify(caller_ptr, src_dst_e)
    elif call_nr == SENDNB:
        result = mini_send(caller_ptr, src_dst_e, m_ptr, NON_BLOCKING)
    else:
        result = EBADCALL

    # Return the result of system call to the caller #
    return result
