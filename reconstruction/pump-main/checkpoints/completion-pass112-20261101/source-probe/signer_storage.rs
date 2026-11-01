#![feature(link_llvm_intrinsics)]
extern crate solana_pubkey;
use solana_pubkey::Pubkey;
use core::mem::{MaybeUninit, size_of, align_of, needs_drop};
const _: [(); 72] = [(); size_of::<MaybeUninit<[u64;9]>>()];
const _: [(); 8] = [(); align_of::<MaybeUninit<[u64;9]>>()];
const _: [(); 32] = [(); size_of::<Pubkey>()];
const _: [(); 1] = [(); align_of::<Pubkey>()];
const _: [(); 16] = [(); size_of::<[&[&[u8]];1]>()];
const _: [(); 8] = [(); align_of::<[&[&[u8]];1]>()];
const _: [(); 16] = [(); size_of::<MaybeUninit<[u64;2]>>()];
const _: [(); 0] = [(); needs_drop::<MaybeUninit<[u64;9]>>() as usize];
const _: [(); 0] = [(); needs_drop::<MaybeUninit<[u64;2]>>() as usize];
const _: [(); 1] = [(); ((4*size_of::<u64>()) % align_of::<[&[&[u8]];1]>() == 0) as usize];
const _: [(); 1] = [(); (4*size_of::<u64>() >= size_of::<Pubkey>()) as usize];
const _: [(); 1] = [(); (4*size_of::<u64>()+size_of::<[&[&[u8]];1]>() <= size_of::<MaybeUninit<[u64;9]>>()) as usize];
extern "C" {
 #[link_name="llvm.var.annotation.p0.p0"] fn vann(p:*const u8,s:*const u8,f:*const u8,l:u32,a:*const u8);
 fn observe_signers(key:*const Pubkey,seeds:*const &[&[u8]],count:usize);
}
#[no_mangle]
pub fn signer_storage_probe(key:Pubkey,authority_bump:u8)->Pubkey {
 let mut hkx=MaybeUninit::<[u64;9]>::uninit();
 let hkb=hkx.as_mut_ptr().cast::<u64>();
 let holder_key:&Pubkey=unsafe{hkb.cast::<Pubkey>().write(key);&*hkb.cast::<Pubkey>()};
 let mut event_signers=MaybeUninit::<[u64;2]>::uninit();
 {
  let event_inner:&[&[u8]]=&[b"__event_authority",&[authority_bump]];
  let es=unsafe{hkb.add(4)}.cast::<[&[&[u8]];1]>();
  unsafe{es.write([event_inner]);vann(event_signers.as_mut_ptr() as *const u8,b"\0".as_ptr(),b"\0".as_ptr(),0,core::ptr::null())};
  unsafe{observe_signers(holder_key,(&*es).as_ptr(),(&*es).len())};
 }
 *holder_key
}
