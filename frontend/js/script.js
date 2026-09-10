/* TRACE-LOOP V2 - IEEE FINAL ROUND FRONTEND */
const API_BASE = "http://127.0.0.1:5000";
const DEMO_DEVICES = {
  TL000001:{device_id:"TL000001",brand:"Dell",model:"Inspiron 15",device_type:"Laptop",serial_number:"DEMO-DL-001",status:"ACTIVE",trust:98},
  TL000002:{device_id:"TL000002",brand:"HP",model:"Pavilion",device_type:"Laptop",serial_number:"DEMO-HP-002",status:"REFURBISHED",trust:96},
  TL000003:{device_id:"TL000003",brand:"Lenovo",model:"ThinkPad",device_type:"Laptop",serial_number:"DEMO-LN-003",status:"TRANSFERRED",trust:99},
  TL000004:{device_id:"TL000004",brand:"Acer",model:"Aspire",device_type:"Laptop",serial_number:"DEMO-AC-004",status:"RECYCLED",trust:100}
};
function registerDevice(){window.location.href="register.html"}
function trackDevice(){window.location.href="track.html"}
function toast(message,type="success"){
  let box=document.getElementById("tlToast");
  if(!box){box=document.createElement("div");box.id="tlToast";document.body.appendChild(box)}
  box.className="tl-toast "+type; box.textContent=message; requestAnimationFrame(()=>box.classList.add("show"));
  setTimeout(()=>box.classList.remove("show"),2800);
}
async function sha256(text){const data=new TextEncoder().encode(text);const hash=await crypto.subtle.digest("SHA-256",data);return [...new Uint8Array(hash)].map(b=>b.toString(16).padStart(2,"0")).join("")}
async function buildDemoHash(){let previous="GENESIS";const events=["REGISTER|TL000001|2026-06-10","TRANSFER|TL000001|2026-06-18","REFURBISH|TL000001|2026-06-25","VERIFY|TL000001|2026-09-10"];for(const event of events)previous=await sha256(previous+"|"+event);return previous}
async function showHash(){const hash=await buildDemoHash();["heroHash","chainHash"].forEach(id=>{const el=document.getElementById(id);if(el)el.textContent=hash.slice(0,20)+"…"+hash.slice(-12)});return hash}
function setDemoDevice(id){const input=document.getElementById("deviceId");if(input){input.value=id;searchDevice()}}
async function verifyIntegrity(){const hash=await showHash();toast("Integrity verified • SHA-256 chain valid");return hash}
async function searchDevice(){
 const input=document.getElementById("deviceId"), id=(input?.value||"").trim().toUpperCase(), result=document.getElementById("deviceResult");
 if(!id){toast("Enter a TRACE-LOOP Device ID","error");return}
 let device=null;
 try{const response=await fetch(`${API_BASE}/devices/${encodeURIComponent(id)}`);if(response.ok){const data=await response.json();if(data.status==="success")device=data.data}}catch(e){console.info("Backend unavailable; demo mode used.")}
 if(!device)device=DEMO_DEVICES[id];
 if(!device){if(result)result.style.display="none";toast("Device not found in the registry","error");return}
 if(result)result.style.display="block";
 const set=(x,v)=>{const el=document.getElementById(x);if(el)el.textContent=v??"—"};
 set("resultName",`${device.brand||""} ${device.model||""}`.trim());set("resultDeviceId",device.device_id);set("resultBrand",device.brand);set("resultModel",device.model);set("resultType",device.device_type);set("resultDeviceStatus",device.status);set("resultStatus",`● ${device.status}`);set("trustScore",`${device.trust||98}/100`);
 if(device.status==="RECYCLED"){set("resultRecyclingStatus","RECYCLED");set("resultRecycler","Verified Recycler");set("resultCertificate","TL-CERT-"+device.device_id.replace("TL",""))}else{set("resultRecyclingStatus","Not completed");set("resultRecycler","Pending");set("resultCertificate","Not issued")}
 await showHash();toast(`Passport verified • ${device.device_id}`);window.scrollTo({top:document.getElementById("deviceResult")?.offsetTop-90||0,behavior:"smooth"})
}
function bindForms(){
 const registerForm=document.getElementById("registerForm");
 if(registerForm)registerForm.addEventListener("submit",async e=>{e.preventDefault();const payload={device_type:document.getElementById("deviceType")?.value,brand:document.getElementById("brand")?.value.trim(),model:document.getElementById("model")?.value.trim(),serial_number:document.getElementById("serialNumber")?.value.trim()};if(!payload.device_type||!payload.brand||!payload.model||!payload.serial_number){toast("Complete all device fields","error");return}try{const r=await fetch(`${API_BASE}/devices`,{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(payload)});const data=await r.json();if(data.status==="success"){toast(`Passport created: ${data.data.device_id}`);registerForm.reset()}else toast(data.error||"Registration failed","error")}catch{toast("Backend unavailable. Start Flask or use demo mode.","error")}});
 const transferForm=document.getElementById("transferForm");
 if(transferForm)transferForm.addEventListener("submit",async e=>{e.preventDefault();const deviceId=document.getElementById("transferDeviceId")?.value.trim(),fromUser=document.getElementById("fromUser")?.value.trim(),toUser=document.getElementById("toUser")?.value.trim();if(!deviceId||!fromUser||!toUser){toast("Complete the transfer details","error");return}try{const r=await fetch(`${API_BASE}/devices/${encodeURIComponent(deviceId)}/transfer`,{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({from_user:Number(fromUser),to_user:Number(toUser)})});const data=await r.json();if(data.status==="success"){toast("Ownership transfer recorded ✓");transferForm.reset()}else toast(data.error||"Transfer failed","error")}catch{toast("Unable to connect to backend","error")}});
 const recyclerForm=document.getElementById("recyclerForm");
 if(recyclerForm)recyclerForm.addEventListener("submit",async e=>{e.preventDefault();const payload={name:document.getElementById("recyclerName")?.value.trim(),organization:document.getElementById("organization")?.value.trim(),contact:document.getElementById("recyclerContact")?.value.trim(),license_number:document.getElementById("licenseNumber")?.value.trim()};if(Object.values(payload).some(v=>!v)){toast("Complete recycler details","error");return}try{const r=await fetch(`${API_BASE}/recyclers`,{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(payload)});const data=await r.json();if(data.status==="success"){toast(`Recycler registered: #${data.data.recycler_id}`);recyclerForm.reset()}else toast(data.error||"Recycler registration failed","error")}catch{toast("Unable to connect to backend","error")}});
}
document.addEventListener("DOMContentLoaded",()=>{bindForms();showHash();const input=document.getElementById("deviceId");if(input)input.addEventListener("keydown",e=>{if(e.key==="Enter")searchDevice()});});
async function verifyRecycler() {

    const recyclerId =
        document.getElementById("verifyRecyclerId")
            .value.trim();


    if (!recyclerId) {

        alert(
            "Please enter Recycler ID."
        );

        return;
    }


    try {

        const response = await fetch(
            `http://127.0.0.1:5000/recyclers/${recyclerId}/verify`,
            {
                method: "POST"
            }
        );


        const result =
            await response.json();


        if (result.status === "success") {

            alert(
                "Recycler verified successfully! ✅\n\n" +
                "Recycler ID: " +
                result.data.recycler_id + "\n" +
                "Status: " +
                result.data.verification_status
            );

        } else {

            alert(
                "Verification failed: " +
                result.error
            );
        }

    } catch (error) {

        console.error(error);

        alert(
            "Unable to connect to the backend."
        );
    }
}


// =====================================
// SEND LAPTOP TO RECYCLER
// =====================================

async function sendToRecycler() {

    const deviceId =
        document.getElementById("recycleDeviceId")
            .value.trim();

    const recyclerId =
        document.getElementById("recycleRecyclerId")
            .value.trim();


    if (!deviceId || !recyclerId) {

        alert(
            "Please enter Device ID and Recycler ID."
        );

        return;
    }


    try {

        const response = await fetch(
            `http://127.0.0.1:5000/devices/${deviceId}/recycle`,
            {
                method: "POST",

                headers: {
                    "Content-Type": "application/json"
                },

                body: JSON.stringify({
                    recycler_id: Number(recyclerId)
                })
            }
        );


        const result =
            await response.json();


        if (result.status === "success") {

            alert(
                "Laptop sent to recycler successfully! ♻️\n\n" +
                "Device ID: " +
                result.data.device_id + "\n" +
                "Recycler ID: " +
                result.data.recycler_id + "\n" +
                "Status: " +
                result.data.status
            );

        } else {

            alert(
                "Failed: " +
                result.error
            );
        }

    } catch (error) {

        console.error(error);

        alert(
            "Unable to connect to the backend."
        );
    }
}


// =====================================
// RECYCLER RECEIVES LAPTOP
// =====================================

async function receiveByRecycler() {

    const deviceId =
        document.getElementById("recycleDeviceId")
            .value.trim();

    const recyclerId =
        document.getElementById("recycleRecyclerId")
            .value.trim();


    if (!deviceId || !recyclerId) {

        alert(
            "Please enter Device ID and Recycler ID."
        );

        return;
    }


    try {

        const response = await fetch(
            `http://127.0.0.1:5000/devices/${deviceId}/receive`,
            {
                method: "POST",

                headers: {
                    "Content-Type": "application/json"
                },

                body: JSON.stringify({
                    recycler_id: Number(recyclerId)
                })
            }
        );


        const result =
            await response.json();


        if (result.status === "success") {

            alert(
                "Laptop received by recycler! 📦\n\n" +
                "Device ID: " +
                result.data.device_id + "\n" +
                "Status: " +
                result.data.status
            );

        } else {

            alert(
                "Failed: " +
                result.error
            );
        }

    } catch (error) {

        console.error(error);

        alert(
            "Unable to connect to the backend."
        );
    }
}


// =====================================
// COMPLETE RECYCLING
// =====================================

async function completeRecycling() {

    const deviceId =
        document.getElementById("recycleDeviceId")
            .value.trim();

    const recyclerId =
        document.getElementById("recycleRecyclerId")
            .value.trim();


    if (!deviceId || !recyclerId) {

        alert(
            "Please enter Device ID and Recycler ID."
        );

        return;
    }


    try {

        const response = await fetch(
            `http://127.0.0.1:5000/devices/${deviceId}/complete-recycling`,
            {
                method: "POST",

                headers: {
                    "Content-Type": "application/json"
                },

                body: JSON.stringify({
                    recycler_id: Number(recyclerId)
                })
            }
        );


        const result =
            await response.json();


        if (result.status === "success") {

            const certificateCard =
                document.getElementById(
                    "certificateCard"
                );

            const certificateResult =
                document.getElementById(
                    "certificateResult"
                );


            if (certificateCard) {
                certificateCard.style.display =
                    "block";
            }


            if (certificateResult) {

                certificateResult.innerHTML =
                    "<strong>Certificate ID:</strong> " +
                    result.data.certificate_id +
                    "<br><br>" +

                    "<strong>Device ID:</strong> " +
                    result.data.device_id +
                    "<br><br>" +

                    "<strong>Recycler ID:</strong> " +
                    result.data.recycler_id +
                    "<br><br>" +

                    "<strong>Status:</strong> " +
                    result.data.status;
            }


            alert(
                "Recycling completed successfully! ♻️\n\n" +
                "Certificate ID: " +
                result.data.certificate_id
            );


        } else {

            alert(
                "Recycling failed: " +
                result.error
            );
        }

    } catch (error) {

        console.error(error);

        alert(
            "Unable to connect to the backend."
        );
    }
}
