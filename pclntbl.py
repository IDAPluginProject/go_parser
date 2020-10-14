#!/usr/bin/env python
# -*- coding: UTF-8 -*-
import idc, idaapi, idautils
import ida_idaapi
idaapi.require("common")


def get_gopclntbl_seg_start_addr():
    seg_start_addr = idc.BADADDR
    # .gopclntab found in (older) PE & ELF binaries, __gopclntab found in macho binaries,
    # runtime.pclntab in .rdata for newer PE binaries
    seg = common.get_seg(['.gopclntab', '__gopclntab'])

    if seg is None:
        seg_start_addr = common.get_seg_start_addr_from_rdata(['runtime.pclntab'])
    else:
        seg_start_addr = seg.start_ea

    return seg_start_addr

class Pclntbl():
    '''
    PcLineTable:
    Refer:
        1. golang.org/s/go12symtab
        2. https://golang.org/src/debug/gosym/pclntab.go

    For an amd64 system, the pclntab symbol begins:

        [4] 0xfffffffb
        [2] 0x00 0x00
        [1] 0x01
        [1] 0x08
        [8] N (size of function symbol table)
        [8] pc0
        [8] func0 offset
        [8] pc1
        [8] func1 offset
        …
        [8] pcN
        [4] int32 offset from start to source file table
        … and then data referred to by offset, in an unspecified order …
    '''
    # Magic number of pclinetable header
    MAGIC = 0xFFFFFFFB

    def __init__(self, start_addr):
        self.start_addr = start_addr
        self.goroot = ""
        self.min_lc = 0 # "instruction size quantum", i.e. minimum length of an instruction code
        self.ptr_sz = 0 # size in bytes of pointers and the predeclared "int", "uint", and "uintptr" types
        self.func_num = 0 # Number of functions
        self.func_tbl_addr = idc.BADADDR
        self.func_tbl_sz = 0 # Size of whole function table
        #self.func_sym_tbl = dict() # pc -> FunctionSymbolTableEntry
        self.end_pc = 0
        self.srcfile_tbl_addr = idc.BADADDR
        self.srcfile_num = 0 # Number of src files
        self.srcfiles = list()

    def parse_hdr(self):
        '''
        Refer: function [go12Init()] in https://golang.org/src/debug/gosym/pclntab.go
        '''
        magic = idc.get_wide_dword(self.start_addr) & 0xFFFFFFFF
        if magic != Pclntbl.MAGIC:
            print(magic, Pclntbl.MAGIC)
            common._error("Invalid pclntbl header magic number!")
            idc.qexit(1)
            #raise Exception("Invalid pclntbl header magic number!")
        idc.create_data(self.start_addr, idc.FF_DWORD, 4, ida_idaapi.BADADDR)
        idc.set_cmt(self.start_addr, "Magic Number",0)
        idc.set_name(self.start_addr, "runtime_symtab", flags=idaapi.SN_FORCE)
        idaapi.auto_wait()

        if idc.get_wide_word(self.start_addr + 4) & 0xFFFF != 0:
            raise Exception("Invalid pclntbl header")
        idc.create_data(self.start_addr + 4,idc.FF_WORD,2, ida_idaapi.BADADDR)

        self.min_lc = idc.get_wide_byte(self.start_addr + 6) & 0xFF
        if (self.min_lc != 1) and (self.min_lc != 2) and (self.min_lc != 4):
            raise Exception("Invalid pclntbl minimum LC!")
        idc.set_cmt(self.start_addr + 6, "instruction size quantum",0)
        idaapi.auto_wait()

        self.ptr_sz = idc.get_wide_byte(self.start_addr + 7) & 0xFF
        if (self.ptr_sz != 4) and (self.ptr_sz != 8):
            raise Exception("Invalid pclntbl pointer size!")
        idc.set_cmt(self.start_addr + 7, "ptr size",0)
        idaapi.auto_wait()

    def parse_funcs(self):
        '''
        Parse function struct and rename function
        '''
        self.func_num = common.read_mem(self.start_addr + 8, forced_addr_sz=self.ptr_sz)
        common._info("Total functions number: %d\n" % self.func_num)

        self.func_tbl_sz = self.func_num * 2 * self.ptr_sz
        funcs_entry = self.start_addr + 8
        self.func_tbl_addr = funcs_entry + self.ptr_sz
        idc.set_cmt(funcs_entry, "Functions number",0)
        idc.set_name(funcs_entry, "funcs_entry", flags=idaapi.SN_FORCE)
        idaapi.auto_wait()
        idc.set_name(self.func_tbl_addr, "pc0", flags=idaapi.SN_FORCE)
        idaapi.auto_wait()

        for func_idx in range(self.func_num):
            curr_addr = self.func_tbl_addr + func_idx * 2 * self.ptr_sz

            func_addr = common.read_mem(curr_addr, forced_addr_sz=self.ptr_sz)
            if not idc.get_func_name(func_addr):
                common._debug("Creating function @ %x" % func_addr)
                idc.del_items(func_addr, idc.DELIT_EXPAND)
                idaapi.auto_wait()
                idc.create_insn(func_addr)
                idaapi.auto_wait()
                if idc.add_func(func_addr):
                    idaapi.auto_wait()
                    common._info("Create function @ 0x%x" % func_addr)

            name_off = common.read_mem(curr_addr + self.ptr_sz, forced_addr_sz=self.ptr_sz)
            name_addr = self.start_addr + self.ptr_sz + name_off
            func_st_addr = name_addr - self.ptr_sz
            func_st = FuncStruct(func_st_addr, self)
            func_st.parse()

            # Make comment for name offset
            idc.set_cmt(curr_addr + self.ptr_sz, "Func Struct @ 0x%x" % func_st_addr,0)
            idaapi.auto_wait()

    def parse_srcfile(self):
        '''
        Parse and extract source all file names
        '''
        srcfile_tbl_off = common.read_mem(self.func_tbl_addr + self.func_tbl_sz + self.ptr_sz, forced_addr_sz=4) & 0xFFFFFFFF
        self.srcfile_tbl_addr = self.start_addr + srcfile_tbl_off
        idc.set_cmt(self.func_tbl_addr + self.func_tbl_sz + self.ptr_sz, \
            "Source file table addr: 0x%x" % self.srcfile_tbl_addr,0)
        idc.set_name(self.srcfile_tbl_addr, "runtime_filetab", flags=idaapi.SN_FORCE)
        idaapi.auto_wait()

        self.srcfile_num = (common.read_mem(self.srcfile_tbl_addr, forced_addr_sz=4) & 0xFFFFFFFF) - 1
        common._info("--------------------------------------------------------------------------------------")
        common._info("Source File paths(Total number: %d, default print results are user-defind files):\n" % self.srcfile_num)
        for idx in range(self.srcfile_num):
            srcfile_off = common.read_mem((idx+1) * 4 + self.srcfile_tbl_addr, forced_addr_sz=4) & 0xFFFFFFFF
            srcfile_addr = self.start_addr + srcfile_off
            srcfile_path = idc.get_strlit_contents(srcfile_addr).decode()
            if srcfile_path is None or len(srcfile_path) == 0:
                common._error("Failed to parse the [%d] src file(off: 0x%x, addr: @ 0x%x)" %\
                    (idx+1, srcfile_off, srcfile_addr))
                continue

            if len(self.goroot) > 0 and (srcfile_path.startswith(self.goroot) or "/pkg/" in srcfile_path or\
                 srcfile_path == "<autogenerated>" or "_cgo_" in srcfile_path or "go/src/git" in srcfile_path):
                # ignore golang std libs and 3rd pkgs
                common._debug(srcfile_path)
            else:
                # User defined function
                self.srcfiles.append(srcfile_path)
                common._info(srcfile_path)

            idc.create_strlit(srcfile_addr, srcfile_addr + len(srcfile_path) + 1)
            idaapi.auto_wait()
            idc.set_cmt((idx+1) * 4 + self.srcfile_tbl_addr, "",0)
            idaapi.add_dref((idx+1) * 4 + self.srcfile_tbl_addr, srcfile_addr, idaapi.dr_O)
            idaapi.auto_wait()
        common._info("--------------------------------------------------------------------------------------")

    def parse(self):
        self.parse_hdr()
        self.parse_funcs()
        idaapi.auto_wait()
        self.goroot = common.get_goroot()
        parse_func_pointer()
        self.parse_srcfile()


class FuncStruct():
    '''
    Old version:
    Refer: golang.org/s/go12symtab

    struct Func
    {
        uintptr      entry;     // start pc
        int32        name;      // name (offset to C string)
        int32        args;      // size of arguments passed to function
        int32        frame;     // size of function frame, including saved caller PC
        int32        pcsp;      // pcsp table (offset to pcvalue table)
        int32        pcfile;    // pcfile table (offset to pcvalue table)
        int32        pcln;      // pcln table (offset to pcvalue table)
        int32        nfuncdata; // number of entries in funcdata list
        int32        npcdata;   // number of entries in pcdata list
    };

    TODO:
    Latest version:
    Refer: https://golang.org/src/runtime/runtime2.go

    // Layout of in-memory per-function information prepared by linker
    // See https://golang.org/s/go12symtab.
    // Keep in sync with linker (../cmd/link/internal/ld/pcln.go:/pclntab)
    // and with package debug/gosym and with symtab.go in package runtime.
    type _func struct {
        entry   uintptr // start pc
        nameoff int32   // function name

        args        int32  // in/out args size
        deferreturn uint32 // offset of start of a deferreturn call instruction from entry, if any.

        pcsp      int32
        pcfile    int32
        pcln      int32
        npcdata   int32
        funcID    funcID  // set for certain special runtime functions
        _         [2]int8 // unused
        nfuncdata uint8   // must be last
    }
    '''
    def __init__(self, addr, pclntbl):
        self.pclntbl = pclntbl
        self.addr = addr
        self.name = ""
        self.args = 0
        self.frame = 0
        self.pcsp = 0
        self.pcfile = 0
        self.pcln = 0
        self.nfuncdata = 0
        self.npcdata = 0

    def parse(self, is_test=False):
        func_addr = common.read_mem(self.addr, forced_addr_sz=self.pclntbl.ptr_sz, read_only=is_test)

        name_addr = common.read_mem(self.addr + self.pclntbl.ptr_sz, forced_addr_sz=4, read_only=is_test) \
            + self.pclntbl.start_addr
        raw_name_str = idc.get_strlit_contents(name_addr)
        if raw_name_str and len(raw_name_str) > 0:
            self.name = common.clean_function_name(raw_name_str)

        if not is_test:
            idc.set_cmt(self.addr, "Func Entry",0)
            idaapi.auto_wait()
            # make comment for func name offset
            idc.set_cmt(self.addr + self.pclntbl.ptr_sz, "Func name offset(Addr @ 0x%x), name string: %s" % (name_addr, raw_name_str),0)
            idaapi.auto_wait()

            # Make name string
            if len(self.name) > 0:
                if idc.create_strlit(name_addr, name_addr + len(raw_name_str) + 1):
                    idaapi.auto_wait()
                    common._debug("Match func_name: %s" % self.name)
                else:
                    common._error("Make func_name_str [%s] failed @0x%x" % (self.name, name_addr))

            # Rename function
            real_func_addr = idaapi.get_func(func_addr)
            if len(self.name) > 0 and real_func_addr is not None:
                if idc.set_name(real_func_addr.start_ea, self.name, flags=idaapi.SN_FORCE):
                    idaapi.auto_wait()
                    common._debug("Rename function 0x%x: %s" % (real_func_addr.start_ea, self.name))
                else:
                    common._error('Failed to rename function @ 0x%x' % real_func_addr.start_ea)

        self.args = common.read_mem(self.addr + self.pclntbl.ptr_sz + 4, forced_addr_sz=4, read_only=is_test)
        self.frame = common.read_mem(self.addr + self.pclntbl.ptr_sz + 2*4, forced_addr_sz=4, read_only=is_test)
        self.pcsp = common.read_mem(self.addr + self.pclntbl.ptr_sz + 3*4, forced_addr_sz=4, read_only=is_test)
        self.pcfile = common.read_mem(self.addr + self.pclntbl.ptr_sz + 4*4, forced_addr_sz=4, read_only=is_test)
        self.pcln = common.read_mem(self.addr + self.pclntbl.ptr_sz + 5*4, forced_addr_sz=4, read_only=is_test)
        self.nfuncdata = common.read_mem(self.addr + self.pclntbl.ptr_sz + 6*4, forced_addr_sz=4, read_only=is_test)
        self.npcdata = common.read_mem(self.addr + self.pclntbl.ptr_sz + 7*4, forced_addr_sz=4, read_only=is_test)

        if not is_test:
            idc.set_cmt(self.addr + self.pclntbl.ptr_sz + 4, "args",0)
            idc.set_cmt(self.addr + self.pclntbl.ptr_sz + 2*4, "frame",0)
            idc.set_cmt(self.addr + self.pclntbl.ptr_sz + 3*4, "pcsp",0)
            idc.set_cmt(self.addr + self.pclntbl.ptr_sz + 4*4, "pcfile",0)
            idc.set_cmt(self.addr + self.pclntbl.ptr_sz + 5*4, "pcln",0)
            idc.set_cmt(self.addr + self.pclntbl.ptr_sz + 6*4, "nfuncdata",0)
            idc.set_cmt(self.addr + self.pclntbl.ptr_sz + 7*4, "npcdata",0)
            idaapi.auto_wait()


# Function pointers are often used instead of passing a direct address to the
# function -- this function names them based off what they're currently named
# to ease reading
#
# lea     rax, main_GetExternIP_ptr <-- pointer to actual function
# mov     [rsp+1C0h+var_1B8], rax <-- loaded as arg for next function
# call    runtime_newproc <-- function is used inside a new process

def parse_func_pointer():
    renamed = 0

    for segea in idautils.Segments():
        for addr in idautils.Functions(segea, idc.get_segm_end(segea)):
        #for addr in idautils.Functions(text_seg.start_ea, text_seg.end_ea):
            name = idc.get_func_name(addr)

            # Look at data xrefs to the function - find the pointer that is located in .rodata
            data_ref = idaapi.get_first_dref_to(addr)
            while data_ref != idc.BADADDR:
                if 'rodata' in idc.get_segm_name(data_ref):
                    # Only rename things that are currently listed as an offset; eg. off_9120B0
                    if 'off_' in idc.get_name(data_ref):
                        if idc.set_name(data_ref, ('%s_ptr' % name), flags=idaapi.SN_FORCE):
                            idaapi.auto_wait()
                            renamed += 1
                        else:
                            common._error('Failed to name pointer @ 0x%02x for %s' % (data_ref, name))

                data_ref = idaapi.get_next_dref_to(addr, data_ref)

    common._info("Rename %d function pointers.\n" % renamed)
