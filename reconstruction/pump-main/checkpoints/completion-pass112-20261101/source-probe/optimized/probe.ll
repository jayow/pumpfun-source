; ModuleID = 'signer_storage.1a7c3f8530556a85-cgu.0'
source_filename = "signer_storage.1a7c3f8530556a85-cgu.0"
target datalayout = "e-m:e-p:64:64-i64:64-n32:64-S128"
target triple = "sbf"

@alloc_7a7952b5e0d15f2b72d0341cef6d4739 = private unnamed_addr constant <{ [17 x i8] }> <{ [17 x i8] c"__event_authority" }>, align 1
@alloc_914b2c69d7eca30497b9feaf15ac92f1 = private unnamed_addr constant <{ [1 x i8] }> zeroinitializer, align 1

; Function Attrs: nounwind
define void @signer_storage_probe(ptr dead_on_unwind noalias nocapture noundef writable writeonly sret([32 x i8]) align 1 dereferenceable(32) %_0, ptr noalias nocapture noundef readonly align 1 dereferenceable(32) %key, i8 noundef %authority_bump) unnamed_addr #0 {
start:
  %_13 = alloca [1 x i8], align 1
  %_9 = alloca [32 x i8], align 8
  %event_signers = alloca [16 x i8], align 8
  %hkx = alloca [72 x i8], align 8
  call void @llvm.lifetime.start.p0(i64 72, ptr nonnull %hkx)
  call void @llvm.memcpy.p0.p0.i64(ptr noundef nonnull align 8 dereferenceable(32) %hkx, ptr noundef nonnull align 1 dereferenceable(32) %key, i64 32, i1 false)
  call void @llvm.lifetime.start.p0(i64 16, ptr nonnull %event_signers)
  call void @llvm.lifetime.start.p0(i64 32, ptr nonnull %_9)
  call void @llvm.lifetime.start.p0(i64 1, ptr nonnull %_13)
  store i8 %authority_bump, ptr %_13, align 1
  store ptr @alloc_7a7952b5e0d15f2b72d0341cef6d4739, ptr %_9, align 8
  %0 = getelementptr inbounds i8, ptr %_9, i64 8
  store i64 17, ptr %0, align 8
  %1 = getelementptr inbounds [2 x { ptr, i64 }], ptr %_9, i64 0, i64 1
  store ptr %_13, ptr %1, align 8
  %2 = getelementptr inbounds [2 x { ptr, i64 }], ptr %_9, i64 0, i64 1, i32 1
  store i64 1, ptr %2, align 8
  %_15 = getelementptr inbounds i64, ptr %hkx, i64 4
  store ptr %_9, ptr %_15, align 8
  %_17.sroa.2.0._15.sroa_idx = getelementptr inbounds i64, ptr %hkx, i64 5
  store i64 2, ptr %_17.sroa.2.0._15.sroa_idx, align 8
  call void @llvm.var.annotation.p0.p0(ptr noundef nonnull %event_signers, ptr noundef nonnull @alloc_914b2c69d7eca30497b9feaf15ac92f1, ptr noundef nonnull @alloc_914b2c69d7eca30497b9feaf15ac92f1, i32 noundef zeroext 0, ptr noundef null) #4
  call void @observe_signers(ptr noundef nonnull %hkx, ptr noundef nonnull %_15, i64 noundef 1) #4
  call void @llvm.lifetime.end.p0(i64 1, ptr nonnull %_13)
  call void @llvm.lifetime.end.p0(i64 32, ptr nonnull %_9)
  call void @llvm.memcpy.p0.p0.i64(ptr noundef nonnull align 1 dereferenceable(32) %_0, ptr noundef nonnull align 8 dereferenceable(32) %hkx, i64 32, i1 false)
  call void @llvm.lifetime.end.p0(i64 16, ptr nonnull %event_signers)
  call void @llvm.lifetime.end.p0(i64 72, ptr nonnull %hkx)
  ret void
}

; Function Attrs: mustprogress nocallback nofree nounwind willreturn memory(argmem: readwrite)
declare void @llvm.memcpy.p0.p0.i64(ptr noalias nocapture writeonly, ptr noalias nocapture readonly, i64, i1 immarg) #1

; Function Attrs: mustprogress nocallback nofree nosync nounwind willreturn memory(inaccessiblemem: readwrite)
declare void @llvm.var.annotation.p0.p0(ptr, ptr, ptr, i32, ptr) unnamed_addr #2

; Function Attrs: nounwind
declare void @observe_signers(ptr noundef, ptr noundef, i64 noundef) unnamed_addr #0

; Function Attrs: mustprogress nocallback nofree nosync nounwind willreturn memory(argmem: readwrite)
declare void @llvm.lifetime.start.p0(i64 immarg, ptr nocapture) #3

; Function Attrs: mustprogress nocallback nofree nosync nounwind willreturn memory(argmem: readwrite)
declare void @llvm.lifetime.end.p0(i64 immarg, ptr nocapture) #3

attributes #0 = { nounwind "target-cpu"="generic" "target-features"="+store-imm,+jmp-ext" }
attributes #1 = { mustprogress nocallback nofree nounwind willreturn memory(argmem: readwrite) }
attributes #2 = { mustprogress nocallback nofree nosync nounwind willreturn memory(inaccessiblemem: readwrite) }
attributes #3 = { mustprogress nocallback nofree nosync nounwind willreturn memory(argmem: readwrite) }
attributes #4 = { nounwind }

!llvm.module.flags = !{!0}
!llvm.ident = !{!1}

!0 = !{i32 8, !"PIC Level", i32 2}
!1 = !{!"rustc version 1.79.0-dev"}
