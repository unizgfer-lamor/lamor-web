---
acronym: cloudSLAM
title: Cooperative cloud based simultaneous localization and mapping in dynamic environments
status: past
logo: /images/projects/cloudslam.png
website: http://cloudslam.fer.hr
funding: 'Unity Through Knowledge Fund (Total budget: €244.470, UNIZG-FER: €244.470)'
pi: Ivan Petrović, Ivan Marković
team:
- Kruno Lenac
- Josip Ćesić
partners:
- University of Waterloo (principal investigator Dana Kulić)
order: 220
---

Over the last several decades, robotics has been a key technology for achieving high productivity and industrial competitiveness. Recently, robotics has begun expanding intensively outside of industrial settings, thus bringing robots closer and closer to humans. The main robotics challenges in the coming decade will be to develop robotic systems that can safely cooperate with humans in arbitrary settings and that are capable of autonomously performing complex tasks in human environments.

One of the main prerequisites for robot autonomous operation in unknown environments populated by humans or other robots is to perform simultaneous localization and mapping (SLAM) so that the robot can infer its position and relate it to other objects of interest. The main focus of the cloudSLAM project is to develop algorithms for solving the SLAM problem such that robots can operate reliably even in highly dynamic environments. This will be achieved through a mathematical framework accurately describing the non-Euclidean geometry of objects moving in space and through robot cooperation via a cloud based service.

The methodology of the proposed project will be grounded in a novel estimation approach based on the recently developed Kalman filter on Lie groups. We will estimate the state of the robot and the tracked objects in six degrees of freedom by representing the state with the special Euclidean group (SE3) and performing filtering directly on the introduced group. This way, the non-Euclidean nature of the orientation components will be intrinsically respected, while their uncertainty will be correctly coupled with the uncertainty of the Euclidean members of the SE3 group through the joint covariance matrix.Since the idea of cloudSLAM is to exploit the moving objects for enhancing the SLAM performance, we will develop algorithms for multiple object detection and tracking using Lie groups and random finite sets theory. This result will then serve as an input to a dynamic SLAM algorithm. The dynamic SLAM will also be based on Lie groups, where we will develop an algorithm based on the exactly sparse delayed state filter so as to keep the computational complexity reasonable. Finally, all the information will be shared by multiple agents through a common cloud-based service, thus enabling robots which have just started operating to immediately exploit the experience of veteran team members. The proposed framework will be tested in coordinated exploration scenarios involving heterogeneous teams moving in dynamic environments, including aerial and ground vehicles and human operators.
