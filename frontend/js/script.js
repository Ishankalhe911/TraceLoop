// =====================================
// TRACE-LOOP FRONTEND SCRIPT
// =====================================


// =====================================
// NAVIGATION
// =====================================

function registerDevice() {
    window.location.href = "register.html";
}


function trackDevice() {
    window.location.href = "track.html";
}


// =====================================
// TRACK DEVICE
// =====================================

async function searchDevice() {

    const deviceId =
        document.getElementById("deviceId").value.trim();

    const deviceResult =
        document.getElementById("deviceResult");

    if (deviceId === "") {
        alert("Please enter a Device ID.");
        return;
    }

    try {

        const response = await fetch(
            `http://127.0.0.1:5000/devices/${deviceId}`
        );

        const result = await response.json();

        console.log("API Response:", result);

        if (result.status !== "success") {

            if (deviceResult) {
                deviceResult.style.display = "none";
            }

            alert("Device not found.");
            return;
        }

        const device = result.data;

        // If the result section exists, display it
        if (deviceResult) {

            deviceResult.style.display = "block";

            const resultName =
                document.getElementById("resultName");

            const resultDeviceId =
                document.getElementById("resultDeviceId");

            const resultStatus =
                document.getElementById("resultStatus");

            const resultBrand =
                document.getElementById("resultBrand");

            const resultModel =
                document.getElementById("resultModel");

            const resultType =
                document.getElementById("resultType");

            const resultDeviceStatus =
                document.getElementById("resultDeviceStatus");

            const resultRecycler =
                document.getElementById("resultRecycler");

            const resultRecyclingStatus =
                document.getElementById("resultRecyclingStatus");

            const resultCertificate =
                document.getElementById("resultCertificate");


            if (resultName) {
                resultName.textContent =
                    device.brand + " " + device.model;
            }

            if (resultDeviceId) {
                resultDeviceId.textContent =
                    device.device_id;
            }

            if (resultBrand) {
                resultBrand.textContent =
                    device.brand;
            }

            if (resultModel) {
                resultModel.textContent =
                    device.model;
            }

            if (resultType) {
                resultType.textContent =
                    device.device_type;
            }

            if (resultDeviceStatus) {
                resultDeviceStatus.textContent =
                    device.status;
            }

            if (resultStatus) {
                resultStatus.textContent =
                    "● " + device.status;
            }


            if (device.status === "RECYCLED") {

                if (resultRecyclingStatus) {
                    resultRecyclingStatus.textContent =
                        "RECYCLED";
                }

                if (resultRecycler) {
                    resultRecycler.textContent =
                        "Verified Recycler";
                }

                if (resultCertificate) {
                    resultCertificate.textContent =
                        "Certificate available";
                }

            } else {

                if (resultRecyclingStatus) {
                    resultRecyclingStatus.textContent =
                        "Not completed";
                }

                if (resultRecycler) {
                    resultRecycler.textContent =
                        "—";
                }

                if (resultCertificate) {
                    resultCertificate.textContent =
                        "—";
                }
            }

        } else {

            // Fallback if old track page is being used
            alert(
                "Device Found!\n\n" +
                "Device ID: " + device.device_id + "\n" +
                "Brand: " + device.brand + "\n" +
                "Model: " + device.model + "\n" +
                "Serial Number: " + device.serial_number + "\n" +
                "Status: " + device.status
            );
        }

    } catch (error) {

        console.error("Track error:", error);

        alert(
            "Unable to connect to the backend.\n\n" +
            "Make sure the Flask server is running."
        );
    }
}


// =====================================
// TRANSFER OWNERSHIP
// =====================================

const transferForm =
    document.getElementById("transferForm");

if (transferForm) {

    transferForm.addEventListener(
        "submit",
        async function(event) {

            event.preventDefault();

            const deviceId =
                document.getElementById("transferDeviceId")
                    .value.trim();

            const fromUser =
                document.getElementById("fromUser")
                    .value.trim();

            const toUser =
                document.getElementById("toUser")
                    .value.trim();


            if (!deviceId || !fromUser || !toUser) {

                alert(
                    "Please complete all required fields."
                );

                return;
            }


            try {

                const response = await fetch(
                    `http://127.0.0.1:5000/devices/${deviceId}/transfer`,
                    {
                        method: "POST",

                        headers: {
                            "Content-Type": "application/json"
                        },

                        body: JSON.stringify({
                            from_user: Number(fromUser),
                            to_user: Number(toUser)
                        })
                    }
                );


                const result =
                    await response.json();


                if (result.status === "success") {

                    alert(
                        "Ownership transferred successfully!\n\n" +
                        "Device ID: " +
                        result.data.device_id + "\n" +
                        "From User: " +
                        result.data.from_user + "\n" +
                        "To User: " +
                        result.data.to_user + "\n" +
                        "Transaction: " +
                        result.data.transaction_type
                    );


                    transferForm.reset();

                } else {

                    alert(
                        "Transfer failed:\n" +
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
    );
}


// =====================================
// REGISTER DEVICE
// =====================================

const registerForm =
    document.getElementById("registerForm");

if (registerForm) {

    registerForm.addEventListener(
        "submit",
        async function(event) {

            event.preventDefault();


            const deviceType =
                document.getElementById("deviceType")
                    .value;

            const brand =
                document.getElementById("brand")
                    .value.trim();

            const model =
                document.getElementById("model")
                    .value.trim();

            const serialNumber =
                document.getElementById("serialNumber")
                    .value.trim();


            if (
                !deviceType ||
                !brand ||
                !model ||
                !serialNumber
            ) {

                alert(
                    "Please fill in all required fields."
                );

                return;
            }


            try {

                const response = await fetch(
                    "http://127.0.0.1:5000/devices",
                    {
                        method: "POST",

                        headers: {
                            "Content-Type": "application/json"
                        },

                        body: JSON.stringify({
                            device_type: deviceType,
                            brand: brand,
                            model: model,
                            serial_number: serialNumber
                        })
                    }
                );


                const result =
                    await response.json();


                if (result.status === "success") {

                    alert(
                        "Laptop registered successfully!\n\n" +
                        "Device ID: " +
                        result.data.device_id + "\n" +
                        "Brand: " +
                        result.data.brand + "\n" +
                        "Model: " +
                        result.data.model + "\n" +
                        "Status: " +
                        result.data.status
                    );


                    registerForm.reset();

                } else {

                    alert(
                        "Registration failed: " +
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
    );
}


// =====================================
// RECYCLER REGISTRATION
// =====================================

const recyclerForm =
    document.getElementById("recyclerForm");

if (recyclerForm) {

    recyclerForm.addEventListener(
        "submit",
        async function(event) {

            event.preventDefault();


            const name =
                document.getElementById("recyclerName")
                    .value.trim();

            const organization =
                document.getElementById("organization")
                    .value.trim();

            const contact =
                document.getElementById("recyclerContact")
                    .value.trim();

            const licenseNumber =
                document.getElementById("licenseNumber")
                    .value.trim();


            if (
                !name ||
                !organization ||
                !contact ||
                !licenseNumber
            ) {

                alert(
                    "Please fill in all recycler details."
                );

                return;
            }


            try {

                const response = await fetch(
                    "http://127.0.0.1:5000/recyclers",
                    {
                        method: "POST",

                        headers: {
                            "Content-Type": "application/json"
                        },

                        body: JSON.stringify({
                            name: name,
                            organization: organization,
                            contact: contact,
                            license_number: licenseNumber
                        })
                    }
                );


                const result =
                    await response.json();


                if (result.status === "success") {

                    alert(
                        "Recycler registered successfully!\n\n" +
                        "Recycler ID: " +
                        result.data.recycler_id + "\n" +
                        "Name: " +
                        result.data.name + "\n" +
                        "License: " +
                        result.data.license_number + "\n" +
                        "Status: " +
                        result.data.verification_status
                    );


                    document.getElementById(
                        "verifyRecyclerId"
                    ).value =
                        result.data.recycler_id;


                    document.getElementById(
                        "recycleRecyclerId"
                    ).value =
                        result.data.recycler_id;


                    recyclerForm.reset();

                } else {

                    alert(
                        "Registration failed: " +
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
    );
}


// =====================================
// VERIFY RECYCLER
// =====================================

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